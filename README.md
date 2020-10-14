# EOL Completion

Adding student units tracking.

# Install

    docker-compose exec lms pip install -e /openedx/requirements/eol_completion
    docker-compose exec lms_worker pip install -e /openedx/requirements/eol_completion

## TESTS
**Prepare tests:**

    > cd .github/
    > docker-compose run lms /openedx/requirements/eol_completion/.github/test.sh

## Notes:

-Only verify if unit childrens are completed with BlockCompletion

-Show Certificate was genereated if this exist in GeneratedCertificate model with status='downloadable'
