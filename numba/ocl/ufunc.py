from __future__ import print_function, absolute_import
import warnings
import numpy as np
from numba import sigutils, ocl
from numba.utils import IS_PY3
from numba.ocl.ocldrv import oclarray

if IS_PY3:
    def _exec(codestr, glbls):
        exec (codestr, glbls)
else:
    eval(compile("""
def _exec(codestr, glbls):
    exec codestr in glbls
""",
                 "<_exec>", "exec"))

vectorizer_stager_source = '''
def __vectorized_%(name)s(%(args)s, __out__):
    __tid__ = __ocl__.get_global_id(0)
    __out__[__tid__] = __core__(%(argitems)s)
'''


def to_dtype(ty):
    return np.dtype(str(ty))


class OclVectorize(object):
    def __init__(self, func, targetoptions={}):
        assert not targetoptions
        self.pyfunc = func
        self.kernelmap = {}  # { arg_dtype: (return_dtype), cudakernel }

    def add(self, sig=None, argtypes=None, restype=None):
        # Handle argtypes
        if argtypes is not None:
            warnings.warn("Keyword argument argtypes is deprecated",
                          DeprecationWarning)
            assert sig is None
            if restype is None:
                sig = tuple(argtypes)
            else:
                sig = restype(*argtypes)
        del argtypes
        del restype

        # compile core as device function
        args, return_type = sigutils.normalize_signature(sig)
        sig = return_type(*args)

        cudevfn = ocl.jit(sig, device=True, inline=True)(self.pyfunc)

        # generate outer loop as kernel
        args = ['a%d' % i for i in range(len(sig.args))]
        funcname = self.pyfunc.__name__
        fmts = dict(name=funcname,
                    args=', '.join(args),
                    argitems=', '.join('%s[__tid__]' % i for i in args))
        kernelsource = vectorizer_stager_source % fmts
        glbl = self.pyfunc.__globals__
        glbl.update({'__ocl__': ocl,
                     '__core__': cudevfn})

        _exec(kernelsource, glbl)

        stager = glbl['__vectorized_%s' % funcname]
        # Force all C contiguous
        kargs = [a[::1] for a in list(sig.args) + [sig.return_type]]
        kernel = ocl.jit(argtypes=kargs)(stager)

        argdtypes = tuple(to_dtype(t) for t in sig.args)
        resdtype = to_dtype(sig.return_type)
        self.kernelmap[tuple(argdtypes)] = resdtype, kernel

    def build_ufunc(self):
        return OclUFuncDispatcher(self.kernelmap)


class OclUFuncDispatcher(object):
    """
    Invoke the Ocl ufunc specialization for the given inputs.
    """

    def __init__(self, types_to_retty_kernels):
        self.functions = types_to_retty_kernels

    @property
    def max_blocksize(self):
        try:
            return self.__max_blocksize
        except AttributeError:
            return 2 ** 30 # a very large number

    @max_blocksize.setter
    def max_blocksize(self, blksz):
        self.__max_blocksize = blksz

    @max_blocksize.deleter
    def max_blocksize(self, blksz):
        del self.__max_blocksize

    def _prepare_inputs(self, args):
        # prepare broadcasted contiguous arrays
        # TODO: Allow strided memory (use mapped memory + strides?)
        # TODO: don't perform actual broadcasting, pass in strides
        #        args = [np.ascontiguousarray(a) for a in args]

        return np.broadcast_arrays(*args)

    def _adjust_dimension(self, broadcast_arrays):
        '''Reshape the broadcasted arrays so that they are all 1D arrays.
        Uses ndarray.ravel() to flatten.  It only copy if necessary.
        '''
        for i, ary in enumerate(broadcast_arrays):
            if ary.ndim > 1:  # flatten multi-dimension arrays
                broadcast_arrays[i] = ary.ravel()  # copy if necessary
        return broadcast_arrays

    def _allocate_output(self, broadcast_arrays, result_dtype):
        return np.empty(shape=broadcast_arrays[0].shape, dtype=result_dtype)

    def __call__(self, *args, **kws):
        """
        *args: numpy arrays or DeviceArrayBase (created by ocl.to_device).
               Cannot mix the two types in one call.

        **kws:
            stream -- opencl queue; when defined, asynchronous mode is used.
            out    -- output array. Can be a numpy array or DeviceArrayBase
                      depending on the input arguments.  Type must match
                      the input arguments.
        """
        accepted_kws = 'stream', 'out'
        unknown_kws = [k for k in kws if k not in accepted_kws]
        assert not unknown_kws, ("Unknown keyword args %s" % unknown_kws)

        stream = kws.get('stream', 0)


        # convert arguments to ndarray if they are not
        args = list(args) # convert to list
        has_device_array_arg = any(oclarray.is_ocl_ndarray(v)
                                   for v in args)

        for i, arg in enumerate(args):
            if not isinstance(arg, np.ndarray) and \
                    not oclarray.is_ocl_ndarray(arg):
                args[i] = np.asarray(arg)

        # get the dtype for each argument
        def _get_dtype(x):
            try:
                return x.dtype
            except AttributeError:
                return np.dtype(type(x))

        dtypes = tuple(_get_dtype(a) for a in args)

        # find the fitting function
        result_dtype, ocl_func = self._get_function_by_dtype(dtypes)

        output = kws['out']
        kargs = list(args) + [output]
        ocl_func.configure(output.shape[0])(*kargs)


    def _determine_output_shape(self, broadcast_arrays):
        return broadcast_arrays[0].shape

    def _get_function_by_dtype(self, dtypes):
        try:
            result_dtype, cuda_func = self.functions[dtypes]
            return result_dtype, cuda_func
        except KeyError:
            raise TypeError("Input dtypes not supported by ufunc %s" %
                            (dtypes,))

    def _determine_element_count(self, broadcast_arrays):
        return np.prod(broadcast_arrays[0].shape)

    def _arguments_requirement(self, args):
        # get shape of all array
        array_shapes = []
        for i, a in enumerate(args):
            if a.strides[0] != 0:
                array_shapes.append((i, a.shape[0]))

        _, ms = array_shapes[0]
        for i, s in array_shapes[1:]:
            if ms != s:
                raise ValueError("arg %d should have length %d" % ms)

    def _determine_dimensions(self, n, max_thread):
        # determine grid and block dimension
        thread_count = int(min(max_thread, n))
        block_count = int((n + max_thread - 1) // max_thread)
        return block_count, thread_count
