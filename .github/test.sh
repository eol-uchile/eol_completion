#!/bin/dash
set -x

pip install zeep==3.4.0
pip install -e git+https://github.com/eol-virtuallabx/eol_custom_reg_form@bcc9233281392e916c789a6e244c933b928bf42b#egg=eol_custom_reg_form
pip install -e /openedx/requirements/eol_completion

cd /openedx/requirements/eol_completion/eol_completion
cp /openedx/edx-platform/setup.cfg .
mkdir test_root
cd test_root/
ln -s /openedx/staticfiles .

cd /openedx/requirements/eol_completion/eol_completion

DJANGO_SETTINGS_MODULE=lms.envs.test EDXAPP_TEST_MONGO_HOST=mongodb pytest tests.py
