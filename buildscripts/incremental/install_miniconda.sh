#!/bin/bash

set -v -e

# Install Miniconda
unamestr=`uname`
if [[ "$unamestr" == 'Linux' ]]; then
    if [[ "$BITS32" == "yes" ]]; then
        wget https://repo.continuum.io/miniconda/Miniconda3-latest-Linux-x86.sh -O miniconda.sh
    else
        if [[ `uname -m ` == 'aarch64' ]]; then 
            wget -q "https://github.com/conda-forge/miniforge/releases/download/4.8.2-1/Miniforge3-4.8.2-1-Linux-aarch64.sh" -O archiconda.sh
        else
            wget https://repo.continuum.io/miniconda/Miniconda3-latest-Linux-x86_64.sh -O miniconda.sh
        fi
    fi
elif [[ "$unamestr" == 'Darwin' ]]; then
    wget https://repo.continuum.io/miniconda/Miniconda3-latest-MacOSX-x86_64.sh -O miniconda.sh
else
  echo Error
fi

if [[ `uname -m ` == 'aarch64' ]]; then 
  chmod +x archiconda.sh
  ./archiconda.sh -b -p $HOME/miniconda
  export PATH="$HOME/miniconda/bin:$PATH"
  hash -r
  conda config --set always_yes yes --set changeps1 no
  conda update -q conda
else
  chmod +x miniconda.sh
  ./miniconda.sh -b
fi
