# licenseme-cli

`licenseme` is a portable Python CLI that generates popular open-source license texts from
canonical SPDX templates. It prompts for the variable portions (copyright owner, year, email,
program name, etc.), infers defaults from your git config, and can run non-interactively using
command-line overrides.

## Usage

```bash
./licenseme MIT
# or after installation
pip install -e .
licenseme Apache-2.0 --defaults --holder "Jane Doe" --year 2024
```

Use `licenseme --list` to see all supported identifiers.
