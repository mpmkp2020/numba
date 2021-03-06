#!/usr/bin/env bash

# PLEASE NOTE: This script has been automatically generated by conda-smithy. Any changes here
# will be lost next time ``conda smithy rerender`` is run. If you would like to make permanent
# changes to this script, consider a proposal to conda-smithy so that other feedstocks can also
# benefit from the improvement.

set -xeo pipefail

FEEDSTOCK_ROOT=$(cd "$(dirname "$0")/../.."; pwd;)
#RECIPE_ROOT="${FEEDSTOCK_ROOT}/recipe"

docker info

# In order for the conda-build process in the container to write to the mounted
# volumes, we need to run with the same id as the host machine, which is
# normally the owner of the mounted volumes, or at least has write permission
export HOST_USER_ID=$(id -u)
# Check if docker-machine is being used (normally on OSX) and get the uid from
# the VM
if hash docker-machine 2> /dev/null && docker-machine active > /dev/null; then
    export HOST_USER_ID=$(docker-machine ssh $(docker-machine active) id -u)
fi

ARTIFACTS="$FEEDSTOCK_ROOT/build_artifacts"
mkdir -p "$ARTIFACTS"
CI='azure'
if [ -z "${CI}" ]; then
    DOCKER_RUN_ARGS="-it "
fi

export UPLOAD_PACKAGES="${UPLOAD_PACKAGES:-True}"
docker run ${DOCKER_RUN_ARGS} \
           -v "${FEEDSTOCK_ROOT}":/home/conda/feedstock_root:rw,z \
           -e TEST_START_INDEX \
           -e TEST_COUNT \
           -e HOST_USER_ID \
           -e CI \
           $DOCKER_IMAGE \
           bash \
           /home/conda/feedstock_root/buildscripts/incremental/build_steps.sh

# verify that the end of the script was reached
test -f "$DONE_CANARY"
