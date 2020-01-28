#!/bin/dash

pip install -e /openedx/requirements/eol_completion

cd /openedx/requirements/eol_completion/eol_completion
cp /openedx/edx-platform/setup.cfg .
mkdir test_root
cd test_root/
ln -s /openedx/staticfiles .

cd /openedx/requirements/eol_completion/eol_completion

DJANGO_SETTINGS_MODULE=lms.envs.test EDXAPP_TEST_MONGO_HOST=mongodb pytest tests.py