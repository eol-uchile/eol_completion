#!/bin/bash

set -e

pip install -e git+https://github.com/eol-uchile/uchileedxlogin@1.0.0#egg=uchileedxlogin
pip install --src /openedx/venv/src -e /openedx/requirements/app

cd /openedx/requirements/app
cp /openedx/edx-platform/setup.cfg .

mkdir test_root
cd test_root/
ln -s /openedx/staticfiles .

cd /openedx/requirements/app

DJANGO_SETTINGS_MODULE=lms.envs.test EDXAPP_TEST_MONGO_HOST=mongodb pytest eol_completion/tests.py \
  && \
  rm -rf test_root
