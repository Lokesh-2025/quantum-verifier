# Contributing

Thanks for your interest in quantum-verifier.

## Getting help and reporting bugs

Open an [issue](https://github.com/Lokesh-2025/quantum-verifier/issues). The
bug report template asks for the details that make a problem reproducible
(your Python and Qiskit versions, the circuit or tool call, and the full error).

## Support expectations

This project is **experimental** and maintained by one person. Expect
breaking changes between releases. Bug reports are read and answered on a
best-effort basis, usually within a week or two. Nothing here is a guarantee
of availability.

## Setting up

```bash
git clone https://github.com/Lokesh-2025/quantum-verifier.git
cd quantum-verifier
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

If you upgrade your operating system or Python, rebuild `.venv` the same way.
The tool needs Qiskit 2.0 or newer and Python 3.12 or newer.

Live IBM and IonQ features need your own API tokens. Copy `.env.example` to
`.env` and fill them in. Never commit `.env`.

## Running the tests

```bash
python -m pytest -q
```

The suite runs offline. Tests that need a live IBM or IonQ account skip
themselves when no token is set, and the few that need the optional `cudaq`
package skip when it isn't installed.

## Sending a change

1. Fork the repository and make a branch.
2. Keep the change focused, and add or update a test for it.
3. Run the tests and make sure they pass.
4. Open a pull request describing what changed and why.

By contributing, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md)
and that your contribution is released under the project's MIT license.
