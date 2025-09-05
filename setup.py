import setuptools

setuptools.setup(
    name="eol_completion",
    version="1.0.0",
    author="Oficina EOL UChile",
    author_email="eol-ing@uchile.cl",
    description="Eol Completion",
    long_description="Eol Completion",
    url="https://eol.uchile.cl",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 2",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    entry_points={
        "lms.djangoapp": [
            "eol_completion = eol_completion.apps:EolCompletionConfig",
        ],
        "openedx.course_tab": [
            "eol_completion = eol_completion.plugins:EolCompletionTab",
        ]
    },
)
