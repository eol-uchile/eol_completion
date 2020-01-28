# EOL Completion

Adding student units tracking.

## TESTS
**Prepare tests:**

    > cd .github/
    > docker-compose run lms /openedx/requirements/eol_completion/.github/test.sh

## Notes:

-Only verify if unit childrens are completed with BlockCompletion

-Show Certificate was genereated if this exist in GeneratedCertificate model
