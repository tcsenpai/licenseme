# licenseme-cli

`licenseme` is a portable Python CLI that generates popular open-source license texts from
canonical SPDX templates. It prompts for the variable portions (copyright owner, year, email,
program name, etc.), infers defaults from your git config, and can run non-interactively using
command-line overrides.

## Installation & Usage

```bash
pip install licenseme-cli
licenseme --list
licenseme Apache-2.0 --defaults --holder "Jane Doe" --year 2024

# local clone workflow
./licenseme MIT
pip install -e .
```

Use `licenseme --list` to see all supported identifiers.
