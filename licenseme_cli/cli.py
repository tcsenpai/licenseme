#!/usr/bin/env python3
"""Interactive SPDX-based license generator."""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence

Context = Dict[str, str]
ValueFactory = Callable[[Context], Optional[str]]
ValueProvider = Callable[[Context], str]

PLACEHOLDER_YEAR = "<year>"
PLACEHOLDER_HOLDER = "<copyright holder>"
PLACEHOLDER_OWNER = "<owner>"
PLACEHOLDER_EMAIL = "<email>"
PLACEHOLDER_PROJECT = "<project name>"
PLACEHOLDER_PROGRAM = "<program name>"
PLACEHOLDER_DESCRIPTION = "<description>"
PLACEHOLDER_URL = "<url>"
PACKAGE_NAME = __package__ or "licenseme_cli"
LICENSES_ROOT = resources.files(PACKAGE_NAME) / "data" / "licenses"


def normalize_license_key(name: str) -> str:
    """Normalize a license selector to simplify alias matching."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def read_git_config(key: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "config", "--get", key],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    return completed.stdout.strip()


def guess_full_name() -> str:
    candidates = [
        os.environ.get(var, "")
        for var in (
            "GIT_AUTHOR_NAME",
            "AUTHOR",
            "FULLNAME",
            "NAME",
            "USER",
            "USERNAME",
        )
    ]
    for value in candidates:
        if value:
            return value
    git_name = read_git_config("user.name")
    return git_name


def guess_email() -> str:
    candidates = [
        os.environ.get(var, "")
        for var in (
            "GIT_AUTHOR_EMAIL",
            "EMAIL",
            "AUTHOR_EMAIL",
        )
    ]
    for value in candidates:
        if value:
            return value
    return read_git_config("user.email")


def default_year(_: Context) -> str:
    return str(_dt.date.today().year)


def default_holder(_: Context) -> Optional[str]:
    return guess_full_name() or None


def default_email(_: Context) -> Optional[str]:
    return guess_email() or None


def default_project_name(_: Context) -> Optional[str]:
    return Path.cwd().name


def ensure_value(text: Optional[str]) -> str:
    return text.strip() if text else ""


@dataclass(frozen=True)
class FieldSpec:
    key: str
    prompt: str
    default_factory: Optional[ValueFactory] = None
    optional: bool = False
    placeholder: Optional[str] = None


@dataclass(frozen=True)
class ReplacementSpec:
    tokens: Sequence[str]
    value: ValueProvider | str


@dataclass(frozen=True)
class LicenseSpec:
    key: str
    name: str
    filename: str
    aliases: Sequence[str]
    fields: Sequence[FieldSpec]
    replacements: Sequence[ReplacementSpec] = ()
    preamble_template: Optional[str] = None
    post_process: Optional[Callable[[Context], None]] = None

    def template_resource(self) -> resources.abc.Traversable:
        return LICENSES_ROOT / self.filename


# Helper functions used by multiple license templates.
def holder_with_email(
    context: Context,
    holder_key: str = "copyright_holder",
    email_key: str = "email",
) -> str:
    holder = context.get(holder_key, "").strip()
    email = context.get(email_key, "").strip() if email_key else ""
    if holder and email:
        return f"{holder} <{email}>"
    return holder


def build_program_tagline(context: Context) -> str:
    name = ensure_value(context.get("program_name"))
    description = ensure_value(context.get("program_description"))
    url = ensure_value(context.get("program_url"))
    email = ensure_value(context.get("email"))
    pieces = [segment for segment in (name, description, url) if segment]
    if email:
        pieces.append(f"<{email}>")
    if pieces:
        return " - ".join(pieces)
    return "This program"


def ensure_sentence(text: str) -> str:
    if not text:
        return text
    return text if text.endswith(".") else f"{text}."


def gpl2_notice_line(context: Context) -> str:
    line = ensure_sentence(build_program_tagline(context))
    return f"     {line} Copyright (C) {context.get('year', '')} {holder_with_email(context)}"


def lgpl21_notice_block(context: Context) -> str:
    tagline = ensure_sentence(build_program_tagline(context))
    header = f"     {tagline}"
    body = f"     Copyright (C) {context.get('year', '')} {holder_with_email(context)}"
    return f"{header}\n{body}"


def load_license_text(spec: LicenseSpec) -> str:
    resource = spec.template_resource()
    if not resource.is_file():
        raise FileNotFoundError(f"Template file not found: {spec.filename}")
    return resource.read_text(encoding="utf-8")


def evaluate_value(provider: ValueProvider | str, context: Context) -> str:
    if callable(provider):
        return provider(context)
    if provider in context:
        return context[provider]
    return str(provider)


def apply_replacements(text: str, replacements: Sequence[ReplacementSpec], context: Context) -> str:
    for repl in replacements:
        value = evaluate_value(repl.value, context)
        for token in repl.tokens:
            text = text.replace(token, value)
    return text


def append_preamble(text: str, template: str, context: Context) -> str:
    try:
        rendered = template.format_map(context)
    except KeyError as exc:
        missing = exc.args[0]
        raise KeyError(f"Missing value '{missing}' required by this template") from exc
    rendered = rendered.strip()
    if rendered:
        return f"{rendered}\n\n{text}"
    return text


def _placeholder_for(field: FieldSpec) -> str:
    if field.placeholder:
        return field.placeholder
    pretty = field.key.replace("_", " ")
    return f"<{pretty}>"


def collect_field_values(
    spec: LicenseSpec,
    skip_prompts: bool,
    overrides: Optional[Context] = None,
) -> Context:
    values: Context = {}
    if overrides:
        for key, value in overrides.items():
            trimmed = ensure_value(value)
            if trimmed:
                values[key] = trimmed
    for field in spec.fields:
        prefilled = ensure_value(values.get(field.key, ""))
        if prefilled:
            values[field.key] = prefilled
            continue
        default = ensure_value(field.default_factory(values) if field.default_factory else "")
        placeholder = _placeholder_for(field)
        if skip_prompts:
            candidate = default or ("" if field.optional else placeholder)
            values[field.key] = candidate
            continue
        prompt = field.prompt
        prompt_default = default or ("" if field.optional else placeholder)
        if prompt_default:
            prompt = f"{prompt} [{prompt_default}]"
        prompt += ": "
        while True:
            try:
                user_input = input(prompt)
            except EOFError:
                user_input = ""
            user_input = user_input.strip()
            if not user_input and prompt_default:
                user_input = prompt_default
            if user_input or field.optional:
                values[field.key] = user_input
                break
            print("This field is required.", file=sys.stderr)
    if spec.post_process:
        spec.post_process(values)
    return values


def render_license(spec: LicenseSpec, context: Context) -> str:
    text = load_license_text(spec)
    if spec.replacements:
        text = apply_replacements(text, spec.replacements, context)
    if spec.preamble_template:
        text = append_preamble(text, spec.preamble_template, context)
    if not text.endswith("\n"):
        text += "\n"
    return text


def display_license_list(specs: Sequence[LicenseSpec]) -> None:
    width = max(len(spec.key) for spec in specs)
    for spec in specs:
        aliases = ", ".join(sorted(set(spec.aliases) - {spec.key}))
        alias_text = f" (aliases: {aliases})" if aliases else ""
        print(f"{spec.key.ljust(width)} - {spec.name}{alias_text}")


# License metadata definitions.
LICENSE_SPECS: Sequence[LicenseSpec] = (
    LicenseSpec(
        key="MIT",
        name="MIT License",
        filename="MIT.txt",
        aliases=("mit", "mitlicense"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
            FieldSpec(
                "email",
                "Contact email (optional)",
                default_factory=default_email,
                optional=True,
                placeholder=PLACEHOLDER_EMAIL,
            ),
        ),
        replacements=(
            ReplacementSpec(("<year>",), "year"),
            ReplacementSpec(("<copyright holders>",), lambda ctx: holder_with_email(ctx)),
        ),
    ),
    LicenseSpec(
        key="AGPL-3.0-only",
        name="GNU AGPL v3 (only)",
        filename="AGPL-3.0-only.txt",
        aliases=("agpl-3.0-only", "agpl3-only"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
            FieldSpec(
                "email",
                "Contact email (optional)",
                default_factory=default_email,
                optional=True,
                placeholder=PLACEHOLDER_EMAIL,
            ),
            FieldSpec(
                "program_name",
                "Program name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROGRAM,
            ),
            FieldSpec(
                "program_description",
                "Program description",
                optional=True,
                placeholder=PLACEHOLDER_DESCRIPTION,
            ),
            FieldSpec(
                "program_url",
                "Project URL (optional)",
                optional=True,
                placeholder=PLACEHOLDER_URL,
            ),
        ),
        replacements=(
            ReplacementSpec(("<year>",), "year"),
            ReplacementSpec(("<name of author>",), lambda ctx: holder_with_email(ctx)),
            ReplacementSpec(
                ("<one line to give the program's name and a brief idea of what it does.>",),
                lambda ctx: build_program_tagline(ctx),
            ),
        ),
    ),
    LicenseSpec(
        key="LGPL-2.1-or-later",
        name="GNU LGPL v2.1 (or later)",
        filename="LGPL-2.1-or-later.txt",
        aliases=("lgpl-2.1", "lgpl2.1", "lgpl-2.1+", "lgpl21-or-later"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
            FieldSpec(
                "email",
                "Contact email (optional)",
                default_factory=default_email,
                optional=True,
                placeholder=PLACEHOLDER_EMAIL,
            ),
            FieldSpec(
                "program_name",
                "Library or program name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROGRAM,
            ),
            FieldSpec(
                "program_description",
                "Program description",
                optional=True,
                placeholder=PLACEHOLDER_DESCRIPTION,
            ),
            FieldSpec(
                "program_url",
                "Project URL (optional)",
                optional=True,
                placeholder=PLACEHOLDER_URL,
            ),
        ),
        replacements=(
            ReplacementSpec(
                (
                    "     one line to give the library's name and an idea of what it does.\n     Copyright (C) year  name of author",
                ),
                lgpl21_notice_block,
            ),
        ),
    ),
    LicenseSpec(
        key="LGPL-2.1-only",
        name="GNU LGPL v2.1 (only)",
        filename="LGPL-2.1-only.txt",
        aliases=("lgpl-2.1-only", "lgpl21"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
            FieldSpec(
                "email",
                "Contact email (optional)",
                default_factory=default_email,
                optional=True,
                placeholder=PLACEHOLDER_EMAIL,
            ),
            FieldSpec(
                "program_name",
                "Library or program name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROGRAM,
            ),
            FieldSpec(
                "program_description",
                "Program description",
                optional=True,
                placeholder=PLACEHOLDER_DESCRIPTION,
            ),
            FieldSpec(
                "program_url",
                "Project URL (optional)",
                optional=True,
                placeholder=PLACEHOLDER_URL,
            ),
        ),
        replacements=(
            ReplacementSpec(
                (
                    "     one line to give the library's name and an idea of what it does.\n     Copyright (C) year  name of author",
                ),
                lgpl21_notice_block,
            ),
        ),
    ),
    LicenseSpec(
        key="GPL-2.0-only",
        name="GNU GPL v2 (only)",
        filename="GPL-2.0-only.txt",
        aliases=("gpl-2.0-only", "gpl2-only"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
            FieldSpec(
                "email",
                "Contact email (optional)",
                default_factory=default_email,
                optional=True,
                placeholder=PLACEHOLDER_EMAIL,
            ),
            FieldSpec(
                "program_name",
                "Program name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROGRAM,
            ),
            FieldSpec(
                "program_description",
                "Program description",
                optional=True,
                placeholder=PLACEHOLDER_DESCRIPTION,
            ),
            FieldSpec(
                "program_url",
                "Project URL (optional)",
                optional=True,
                placeholder=PLACEHOLDER_URL,
            ),
        ),
        replacements=(
            ReplacementSpec(
                ("     one line to give the program's name and an idea of what it does. Copyright (C) yyyy name of author",),
                gpl2_notice_line,
            ),
        ),
    ),
    LicenseSpec(
        key="GPL-3.0-only",
        name="GNU GPL v3 (only)",
        filename="GPL-3.0-only.txt",
        aliases=("gpl-3.0-only", "gpl3-only"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
            FieldSpec(
                "email",
                "Contact email (optional)",
                default_factory=default_email,
                optional=True,
                placeholder=PLACEHOLDER_EMAIL,
            ),
            FieldSpec(
                "program_name",
                "Program name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROGRAM,
            ),
            FieldSpec(
                "program_description",
                "Program description",
                optional=True,
                placeholder=PLACEHOLDER_DESCRIPTION,
            ),
            FieldSpec(
                "program_url",
                "Project URL (optional)",
                optional=True,
                placeholder=PLACEHOLDER_URL,
            ),
        ),
        replacements=(
            ReplacementSpec(("<year>",), "year"),
            ReplacementSpec(("<name of author>",), lambda ctx: holder_with_email(ctx)),
            ReplacementSpec(("<program>",), "program_name"),
            ReplacementSpec(
                ("<one line to give the program's name and a brief idea of what it does.>",),
                lambda ctx: build_program_tagline(ctx),
            ),
        ),
    ),
    LicenseSpec(
        key="CC0-1.0",
        name="Creative Commons CC0 1.0 Universal",
        filename="CC0-1.0.txt",
        aliases=("cc0", "cc0-1.0", "creative-commons-zero"),
        fields=(
            FieldSpec(
                "project_name",
                "Project name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROJECT,
            ),
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Author or holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
        ),
        preamble_template="{project_name}\nCopyright (c) {year} {copyright_holder}",
    ),
    LicenseSpec(
        key="BSL-1.0",
        name="Boost Software License 1.0",
        filename="BSL-1.0.txt",
        aliases=("bsl", "boost", "boost-1.0"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
        ),
        preamble_template="Copyright (c) {year} {copyright_holder}",
    ),
    LicenseSpec(
        key="ISC",
        name="ISC License",
        filename="ISC.txt",
        aliases=("isc",),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
        ),
        preamble_template="Copyright (c) {year} {copyright_holder}",
    ),
    LicenseSpec(
        key="Apache-2.0",
        name="Apache License 2.0",
        filename="Apache-2.0.txt",
        aliases=("apache", "apache2", "apache-2", "apache20"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
            FieldSpec(
                "email",
                "Contact email (optional)",
                default_factory=default_email,
                optional=True,
                placeholder=PLACEHOLDER_EMAIL,
            ),
        ),
        replacements=(
            ReplacementSpec(("[yyyy]",), "year"),
            ReplacementSpec(("[name of copyright owner]",), lambda ctx: holder_with_email(ctx)),
        ),
    ),
    LicenseSpec(
        key="BSD-3-Clause",
        name="BSD 3-Clause License",
        filename="BSD-3-Clause.txt",
        aliases=("bsd3", "bsd-3", "bsd-3-clause"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "owner",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_OWNER,
            ),
        ),
        replacements=(
            ReplacementSpec(("<year>",), "year"),
            ReplacementSpec(("<owner>",), lambda ctx: holder_with_email(ctx, holder_key="owner")),
        ),
    ),
    LicenseSpec(
        key="BSD-2-Clause",
        name="BSD 2-Clause License",
        filename="BSD-2-Clause.txt",
        aliases=("bsd2", "bsd-2", "simplifiedbsd"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "owner",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_OWNER,
            ),
        ),
        replacements=(
            ReplacementSpec(("<year>",), "year"),
            ReplacementSpec(("<owner>",), lambda ctx: holder_with_email(ctx, holder_key="owner")),
        ),
    ),
    LicenseSpec(
        key="GPL-3.0-or-later",
        name="GNU GPL v3 (or later)",
        filename="GPL-3.0-or-later.txt",
        aliases=("gpl3", "gpl-3", "gplv3", "gpl-3.0"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
            FieldSpec(
                "email",
                "Contact email (optional)",
                default_factory=default_email,
                optional=True,
                placeholder=PLACEHOLDER_EMAIL,
            ),
            FieldSpec(
                "program_name",
                "Program name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROGRAM,
            ),
            FieldSpec(
                "program_description",
                "Program description",
                optional=True,
                placeholder=PLACEHOLDER_DESCRIPTION,
            ),
            FieldSpec(
                "program_url",
                "Project URL (optional)",
                optional=True,
                placeholder=PLACEHOLDER_URL,
            ),
        ),
        replacements=(
            ReplacementSpec(("<year>",), "year"),
            ReplacementSpec(("<name of author>",), lambda ctx: holder_with_email(ctx)),
            ReplacementSpec(("<program>",), "program_name"),
            ReplacementSpec(
                ("<one line to give the program's name and a brief idea of what it does.>",),
                lambda ctx: build_program_tagline(ctx),
            ),
        ),
    ),
    LicenseSpec(
        key="GPL-2.0-or-later",
        name="GNU GPL v2 (or later)",
        filename="GPL-2.0-or-later.txt",
        aliases=("gpl2", "gpl-2", "gplv2", "gpl-2.0"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
            FieldSpec(
                "email",
                "Contact email (optional)",
                default_factory=default_email,
                optional=True,
                placeholder=PLACEHOLDER_EMAIL,
            ),
            FieldSpec(
                "program_name",
                "Program name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROGRAM,
            ),
            FieldSpec(
                "program_description",
                "Program description",
                optional=True,
                placeholder=PLACEHOLDER_DESCRIPTION,
            ),
            FieldSpec(
                "program_url",
                "Project URL (optional)",
                optional=True,
                placeholder=PLACEHOLDER_URL,
            ),
        ),
        replacements=(
            ReplacementSpec(
                ("     one line to give the program's name and an idea of what it does. Copyright (C) yyyy name of author",),
                gpl2_notice_line,
            ),
        ),
    ),
    LicenseSpec(
        key="LGPL-3.0-or-later",
        name="GNU LGPL v3 (or later)",
        filename="LGPL-3.0-or-later.txt",
        aliases=("lgpl3", "lgpl-3", "lgplv3"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
            FieldSpec(
                "email",
                "Contact email (optional)",
                default_factory=default_email,
                optional=True,
                placeholder=PLACEHOLDER_EMAIL,
            ),
            FieldSpec(
                "program_name",
                "Program or library name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROGRAM,
            ),
            FieldSpec(
                "program_description",
                "Program description",
                optional=True,
                placeholder=PLACEHOLDER_DESCRIPTION,
            ),
            FieldSpec(
                "program_url",
                "Project URL (optional)",
                optional=True,
                placeholder=PLACEHOLDER_URL,
            ),
        ),
        replacements=(
            ReplacementSpec(("<year>",), "year"),
            ReplacementSpec(("<name of author>",), lambda ctx: holder_with_email(ctx)),
            ReplacementSpec(("<program>",), "program_name"),
            ReplacementSpec(
                ("<one line to give the program's name and a brief idea of what it does.>",),
                lambda ctx: build_program_tagline(ctx),
            ),
        ),
    ),
    LicenseSpec(
        key="AGPL-3.0-or-later",
        name="GNU AGPL v3 (or later)",
        filename="AGPL-3.0-or-later.txt",
        aliases=("agpl3", "agpl-3", "agplv3"),
        fields=(
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
            FieldSpec(
                "email",
                "Contact email (optional)",
                default_factory=default_email,
                optional=True,
                placeholder=PLACEHOLDER_EMAIL,
            ),
            FieldSpec(
                "program_name",
                "Program name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROGRAM,
            ),
            FieldSpec(
                "program_description",
                "Program description",
                optional=True,
                placeholder=PLACEHOLDER_DESCRIPTION,
            ),
            FieldSpec(
                "program_url",
                "Project URL (optional)",
                optional=True,
                placeholder=PLACEHOLDER_URL,
            ),
        ),
        replacements=(
            ReplacementSpec(("<year>",), "year"),
            ReplacementSpec(("<name of author>",), lambda ctx: holder_with_email(ctx)),
            ReplacementSpec(
                ("<one line to give the program's name and a brief idea of what it does.>",),
                lambda ctx: build_program_tagline(ctx),
            ),
        ),
    ),
    LicenseSpec(
        key="MPL-2.0",
        name="Mozilla Public License 2.0",
        filename="MPL-2.0.txt",
        aliases=("mpl", "mpl2"),
        fields=(
            FieldSpec(
                "project_name",
                "Project name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROJECT,
            ),
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
        ),
        preamble_template="{project_name}\nCopyright (c) {year} {copyright_holder}",
    ),
    LicenseSpec(
        key="EPL-2.0",
        name="Eclipse Public License 2.0",
        filename="EPL-2.0.txt",
        aliases=("epl", "epl2"),
        fields=(
            FieldSpec(
                "project_name",
                "Project name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROJECT,
            ),
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Copyright holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
        ),
        preamble_template="{project_name}\nCopyright (c) {year} {copyright_holder}",
    ),
    LicenseSpec(
        key="Unlicense",
        name="The Unlicense",
        filename="Unlicense.txt",
        aliases=("unlicense", "public-domain"),
        fields=(
            FieldSpec(
                "project_name",
                "Project name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROJECT,
            ),
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Author or holder",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
        ),
        preamble_template="{project_name}\nCopyright (c) {year} {copyright_holder}",
    ),
    LicenseSpec(
        key="WTFPL",
        name="Do What The F*ck You Want To Public License",
        filename="WTFPL.txt",
        aliases=("wtfpl",),
        fields=(
            FieldSpec(
                "project_name",
                "Project name",
                default_factory=default_project_name,
                placeholder=PLACEHOLDER_PROJECT,
            ),
            FieldSpec("year", "Copyright year", default_factory=default_year, placeholder=PLACEHOLDER_YEAR),
            FieldSpec(
                "copyright_holder",
                "Author",
                default_factory=default_holder,
                placeholder=PLACEHOLDER_HOLDER,
            ),
        ),
        preamble_template="{project_name}\nCopyright (c) {year} {copyright_holder}",
    ),
)

LICENSE_MAP = {normalize_license_key(spec.key): spec for spec in LICENSE_SPECS}
for spec in LICENSE_SPECS:
    for alias in spec.aliases:
        LICENSE_MAP[normalize_license_key(alias)] = spec


def resolve_spec(name: str) -> LicenseSpec:
    key = normalize_license_key(name)
    spec = LICENSE_MAP.get(key)
    if not spec:
        raise KeyError(f"Unsupported license '{name}'. Use --list to see supported identifiers.")
    return spec


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate popular open source licenses from SPDX templates.",
    )
    parser.add_argument("license", nargs="?", help="License identifier or alias (e.g. MIT, Apache-2.0)")
    parser.add_argument("-o", "--output", help="Write the generated license to this path")
    parser.add_argument("-f", "--force", action="store_true", help="Overwrite the output file if it exists")
    parser.add_argument("--list", action="store_true", help="List supported licenses and exit")
    parser.add_argument(
        "--defaults",
        action="store_true",
        help="Skip prompts by using default values wherever possible",
    )
    parser.add_argument("--year", help="Override the copyright year")
    parser.add_argument("--holder", help="Override the copyright holder/author")
    parser.add_argument("--owner", help="Override owner fields (e.g. BSD)")
    parser.add_argument("--email", help="Override contact email")
    parser.add_argument("--program-name", help="Override the program name for GPL-style notices")
    parser.add_argument("--program-description", help="Override the one-line description")
    parser.add_argument("--program-url", help="Override the project URL")
    parser.add_argument("--project-name", help="Override the project name used in preambles")
    parser.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        default=[],
        help="Set an arbitrary field (repeatable)",
    )
    return parser.parse_args(argv)


def build_cli_overrides(args: argparse.Namespace) -> Context:
    overrides: Context = {}

    def push(value: Optional[str], *keys: str) -> None:
        if value is None:
            return
        trimmed = value.strip()
        if not trimmed:
            return
        for key in keys:
            overrides[key] = trimmed

    push(args.year, "year")
    push(args.holder, "copyright_holder", "owner")
    push(args.owner, "owner")
    push(args.email, "email")
    push(args.program_name, "program_name")
    push(args.program_description, "program_description")
    push(args.program_url, "program_url")
    push(args.project_name, "project_name")

    for assignment in getattr(args, "set", []) or []:
        if "=" not in assignment:
            raise ValueError(f"Invalid --set value '{assignment}'. Expected KEY=VALUE.")
        key, value = assignment.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("Override key cannot be empty.")
        overrides[key] = value.strip()

    return overrides


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.list:
        display_license_list(LICENSE_SPECS)
        return 0
    if not args.license:
        print("No license specified. Use --list to see available options.", file=sys.stderr)
        return 1
    try:
        spec = resolve_spec(args.license)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    skip_prompts = bool(args.defaults or not sys.stdin.isatty())
    try:
        overrides = build_cli_overrides(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        context = collect_field_values(spec, skip_prompts, overrides=overrides)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        output_text = render_license(spec, context)
    except Exception as exc:  # pragma: no cover - surfaced to end user
        print(f"Failed to render license: {exc}", file=sys.stderr)
        return 1
    if args.output:
        path = Path(args.output).expanduser()
        if path.exists() and not args.force:
            print(f"Refusing to overwrite existing file: {path}. Use --force to override.", file=sys.stderr)
            return 1
        path.write_text(output_text, encoding="utf-8")
    else:
        sys.stdout.write(output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
