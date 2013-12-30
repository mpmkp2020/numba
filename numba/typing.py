import sys
from collections import namedtuple
from numba import types


def _type_distance(domain, first, second):
    if first in domain and second in domain:
        return domain.index(first) - domain.index(second)


class Context(object):
    """A typing context for storing function typing constrain template.


    """
    def __init__(self, type_lattice=None):
        self.type_lattice = type_lattice or types.type_lattice
        self.functions = {}
        self.attributes = {}
        self.load_builtins()

    def resolve_function_type(self, func, args, kws):
        ft = self.functions[func]
        return ft.apply(args, kws)

    def resolve_getattr(self, value, attr):
        try:
            attrinfo = self.attributes[value]
        except KeyError:
            if value.is_parametric:
                attrinfo = self.attributes[type(value)]
            else:
                raise

        return attrinfo.resolve(value, attr)

    def resolve_setitem(self, target, index, value):
        args = target, index, value
        kws = ()
        return self.resolve_function_type("setitem", args, kws)

    def load_builtins(self):
        for ftcls in BUILTINS:
            self.insert_function(ftcls(self))
        for ftcls in BUILTIN_ATTRS:
            self.insert_attributes(ftcls(self))

    def insert_attributes(self, at):
        key = at.key
        assert key not in self.functions, "Duplicated attributes template"
        self.attributes[key] = at

    def insert_function(self, ft):
        key = ft.key
        assert key not in self.functions, "Duplicated function template"
        self.functions[key] = ft

    def type_distance(self, fromty, toty):
        if fromty == toty:
            return 0

        # if types.any == toty:
        #     return 0
        #
        # if isinstance(toty, types.Kind) and isinstance(fromty, toty.of):
        #     return 0

        return self.type_lattice.get((fromty, toty))

    def unify_types(self, *types):
        return reduce(self.unify_pairs, types)

    def unify_pairs(self, first, second):
        """
        Choose PyObject type as the abstract if we fail to determine a concrete
        type.
        """
        # TODO: should add an option to reject unsafe type conversion
        d = self.type_distance(fromty=first, toty=second)
        if d is None:
            return types.pyobject
        elif d >= 0:
            # A promotion from first -> second
            return second
        else:
            # A demontion from first -> second
            return first


def _uses_downcast(dists):
    for d in dists:
        if d < 0:
            return True
    return False


def _sum_downcast(dists):
    c = 0
    for d in dists:
        if d < 0:
            c += abs(d)
    return c


class FunctionTemplate(object):
    """
    A function typing template
    """
    __slots__ = 'context'

    def __init__(self, context):
        self.context = context

    def apply(self, args, kws):
        cases = getattr(self, 'cases', None)
        if cases:
            upcast, downcast = self._find_compatible_definitions(cases, args,
                                                                 kws)
            return self._select_best_definition(upcast, downcast, args, kws,
                                                cases)

        generic = getattr(self, "generic", None)
        if generic:
            return generic(args, kws)
        raise NotImplementedError

    def apply_case(self, case, args, kws):
        """
        Returns a tuple of type distances for each arguments
        or return None if not match.
        """
        assert not kws, "Keyword argument is not supported, yet"
        if len(case.args) != len(args):
            # Number of arguments mismatch
            return None
        distances = []
        for formal, actual in zip(case.args, args):
            tdist = self.context.type_distance(toty=formal, fromty=actual)
            if tdist is None:
                return
            distances.append(tdist)
        return tuple(distances)

    def _find_compatible_definitions(self, cases, args, kws):
        upcast = []
        downcast = []
        for case in cases:
            dists = self.apply_case(case, args, kws)
            if dists is not None:
                if _uses_downcast(dists):
                    downcast.append((dists, case))
                else:
                    upcast.append((sum(dists), case))
        return upcast, downcast

    def _select_best_definition(self, upcast, downcast, args, kws, cases):
        if upcast:
            return self._select_best_upcast(upcast)
        elif downcast:
            return self._select_best_downcast(downcast)

    def _select_best_downcast(self, downcast):
        assert downcast
        if len(downcast) == 1:
            # Exactly one definition with downcasting
            return downcast[0][1]
        else:
            downdist = sys.maxint
            leasts = []
            for dists, case in downcast:
                n = _sum_downcast(dists)
                if n < downdist:
                    downdist = n
                    leasts = [(dists, case)]
                elif n == downdist:
                    leasts.append((dists, case))

            if len(leasts) == 1:
                return leasts[0][1]
            else:
                # Need to further decide which downcasted version?
                raise TypeError("Ambiguous overloading: %s" %
                                [c for _, c in leasts])

    def _select_best_upcast(self, upcast):
        assert upcast
        if len(upcast) == 1:
            # Exactly one definition without downcasting
            return upcast[0][1]
        else:
            assert len(upcast) > 1
            first = min(upcast)
            upcast.remove(first)
            second = min(upcast)
            if first[0] < second[0]:
                return first[1]
            else:
                raise TypeError("Ambiguous overloading: %s and %s" % (
                    first[1], second[1]))


class Signature(object):
    __slots__ = 'return_type', 'args'

    def __init__(self, return_type, args):
        self.return_type = return_type
        self.args = args

    def __hash__(self):
        return hash(self.args)

    def __eq__(self, other):
        if isinstance(other, Signature):
            return self.args == other.args

    def __ne__(self, other):
        return not (self == other)

    def __repr__(self):
        return "%s -> %s" % (self.args, self.return_type)


def signature(return_type, *args):
    return Signature(return_type, args)


BUILTINS = []
BUILTIN_ATTRS = []

def builtin(template):
    BUILTINS.append(template)
    return template


def builtin_attr(template):
    BUILTIN_ATTRS.append(template)
    return template


#-------------------------------------------------------------------------------

@builtin
class Range(FunctionTemplate):
    key = types.range_type
    cases = [
        signature(types.range_state32_type, types.int32),
        signature(types.range_state32_type, types.int32, types.int32),
        signature(types.range_state64_type, types.int64),
        signature(types.range_state64_type, types.int64, types.int64),
    ]


@builtin
class GetIter(FunctionTemplate):
    key = "getiter"
    cases = [
        signature(types.range_iter32_type, types.range_state32_type),
        signature(types.range_iter64_type, types.range_state64_type),
    ]


@builtin
class IterNext(FunctionTemplate):
    key = "iternext"
    cases = [
        signature(types.int32, types.range_iter32_type),
        signature(types.int64, types.range_iter64_type),
    ]


@builtin
class IterValid(FunctionTemplate):
    key = "itervalid"
    cases = [
        signature(types.boolean, types.range_iter32_type),
        signature(types.boolean, types.range_iter64_type),
    ]


class BinOp(FunctionTemplate):
    cases = [
        signature(types.int32, types.int32, types.int32),
        signature(types.int64, types.int64, types.int64),
        signature(types.float32, types.float32, types.float32),
        signature(types.float64, types.float64, types.float64),
    ]


@builtin
class BinOpAdd(BinOp):
    key = "+"


@builtin
class BinOpSub(BinOp):
    key = "-"


@builtin
class BinOpMul(BinOp):
    key = "*"


@builtin
class BinOpDiv(BinOp):
    key = "/?"


class CmpOp(FunctionTemplate):
    cases = [
        signature(types.boolean, types.int32, types.int32),
        signature(types.boolean, types.int64, types.int64),
        signature(types.boolean, types.float32, types.float32),
        signature(types.boolean, types.float64, types.float64),
    ]


@builtin
class CmpOpLt(CmpOp):
    key = '<'


@builtin
class CmpOpLe(CmpOp):
    key = '<='


@builtin
class CmpOpGt(CmpOp):
    key = '>'


@builtin
class CmpOpGe(CmpOp):
    key = '>='


@builtin
class CmpOpEq(CmpOp):
    key = '=='


@builtin
class CmpOpNe(CmpOp):
    key = '!='


@builtin
class GetItem(FunctionTemplate):
    key = "getitem"
    # cases = [
    #     signature(types.any, types.UniTuple, types.any),
    #     signature(types.any, types.Array, types.any),
    # ]
    def generic(self, args, kws):
        assert not kws
        base = args[0]
        if hasattr(base, "getitem"):
            retty, indty = base.getitem()
            case = signature(retty, base, indty)
            m = self.apply_case(case, args, kws)
            if m is not None:
                return case
        else:
            raise NotImplementedError

@builtin
class SetItem(FunctionTemplate):
    key = "setitem"

    def generic(self, args, kws):
        assert not kws
        base, index, value = args
        if hasattr(base, 'setitem'):
            indty, valty = base.setitem()
            sig = signature(types.none, base, indty, valty)
            return sig
        else:
            raise NotImplementedError


#-------------------------------------------------------------------------------


class AttributeTemplate(object):
    def __init__(self, context):
        self.context = context

    def resolve(self, value, attr):
        fn = getattr(self, "resolve_%s" % attr, None)
        if fn is None:
            raise NotImplementedError(value, attr)
        return fn(value)


@builtin_attr
class ArrayAttribute(AttributeTemplate):
    key = types.Array

    def resolve_shape(self, value):
        return types.UniTuple(types.intp, value.ndim)

