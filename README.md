# EOL Completion

Adding student units tracking.

# Install

    docker-compose exec lms pip install -e /openedx/requirements/eol_completion
    docker-compose exec lms_worker pip install -e /openedx/requirements/eol_completion

## Translation

**Install**

    docker run -it --rm -w /code -v $(pwd):/code python:3.8 bash
    pip install -r requirements.txt
    make create_translations_catalogs
    add your translation in .po files

**Compile**

    docker run -it --rm -w /code -v $(pwd):/code python:3.8 bash
    pip install -r requirements.txt
    make compile_translations

**Update**

    docker run -it --rm -w /code -v $(pwd):/code python:3.8 bash
    pip install -r requirements.txt
    make update_translations


# Configuration

Edit *production.py* in *lms settings* and set  time in cache and limit student.

    EOL_COMPLETION_TIME_CACHE = ENV_TOKENS.get('EOL_COMPLETION_TIME_CACHE', 300)
    EOL_COMPLETION_LIMIT_STUDENT = ENV_TOKENS.get('EOL_COMPLETION_LIMIT_STUDENT', 10000)

## TESTS
**Prepare tests:**

    > cd .github/
    > docker-compose run lms /openedx/requirements/eol_completion/.github/test.sh

## Notes:

-Only verify if unit childrens are completed with BlockCompletion

-Show Certificate was genereated if this exist in GeneratedCertificate model with status='downloadable'
