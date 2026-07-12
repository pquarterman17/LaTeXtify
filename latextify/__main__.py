"""Run the CLI as ``python -m latextify`` (alias for the ``latextify`` script).

Handy when the console script isn't on PATH -- e.g. the usage examples in
``examples/`` invoke ``sys.executable -m latextify`` so they work from any
interpreter that can import the package, no matter how it was installed.
"""

from latextify.cli import main

if __name__ == "__main__":
    main()
