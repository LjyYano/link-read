"""Collect original notices for a release; run with the sidecar build's Python.

No license is inferred from an SPDX identifier. Missing text aborts packaging.
The inventory is an attribution aid, not a determination of license compliance.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


DESKTOP = Path(__file__).resolve().parents[1]
ROOT = DESKTOP.parent
NOTICE_NAME = re.compile(r"^(?:unlicen[cs]e|licen[cs]e|copying|copyright|notice|authors|third[-_]?party)(?:$|[._-])", re.I)
LICENSE_TEXT_NAME = re.compile(r"(?:^|[./_-])(?:unlicen[cs]e|licen[cs]e|copying)(?:$|[./_-])", re.I)
CODE_SUFFIXES = {".py", ".pyc", ".rs", ".js", ".ts", ".json", ".so", ".dll", ".dylib", ".exe"}


def notice_files(directory: Path) -> list[Path]:
    """Retain nested notice directories without collecting another npm package."""
    return sorted(path for path in directory.rglob("*") if path.is_file()
                  and "node_modules" not in path.relative_to(directory).parts
                  and ".git" not in path.relative_to(directory).parts
                  and path.suffix.lower() not in CODE_SUFFIXES
                  and (NOTICE_NAME.match(path.name)
                       or any(part.lower() in {"licenses", "licences"}
                              for part in path.relative_to(directory).parts[:-1])))


def bundled_python_distributions(sidecar_dir: Path) -> set[str]:
    """Discover frozen Python distribution names from *.dist-info / *.egg-info in a sidecar dir."""
    names: set[str] = set()
    for pattern in ("*.dist-info", "*.egg-info"):
        for entry in sidecar_dir.rglob(pattern):
            if not entry.is_dir():
                continue
            raw_name = None
            try:
                raw_name = metadata.PathDistribution(entry).metadata.get("Name")
            except OSError:
                raw_name = None
            if not raw_name:
                raw_name = re.split(r"-[0-9]", entry.stem, maxsplit=1)[0]
            if raw_name:
                names.add(canonicalize_name(raw_name))
    return names


def bundled_native_libraries(sidecar_dir: Path) -> list[str]:
    """List native shared libraries bundled inside the --onedir sidecar."""
    return sorted(
        path.relative_to(sidecar_dir).as_posix()
        for path in sidecar_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".dylib", ".so"}
    )


class Collector:
    def __init__(self, output: Path, root: Path = ROOT):
        self.output = output
        self.root = root
        self.desktop = root / "desktop"
        self.components: list[dict] = []

    def add(self, ecosystem: str, name: str, version: str, license_id: str | None,
            sources: list[str], files: list[tuple[Path, str]], required_notices: list[str] | None = None) -> None:
        """Copy full original bytes and record hashes; optional overrides are versioned."""
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        safe_version = re.sub(r"[^A-Za-z0-9._-]", "_", version)
        override = self.desktop / "licenses/overrides" / ecosystem / safe_name / safe_version
        if override.exists():
            provenance = override / "SOURCE.json"
            if not provenance.is_file():
                raise RuntimeError(f"{ecosystem} {name}@{version}: {provenance} must record the upstream source URL")
            info = json.loads(provenance.read_text(encoding="utf-8"))
            if info.get("version") != version or not str(info.get("source", "")).startswith("https://"):
                raise RuntimeError(f"{ecosystem} {name}@{version}: SOURCE.json requires this exact version and an HTTPS source URL")
            sources.append(info["source"])
            files.extend((path, "upstream/" + path.relative_to(override).as_posix()) for path in notice_files(override))
            files.append((provenance, "upstream/SOURCE.json"))
        # A provenance record alone is not a license.
        if not any(LICENSE_TEXT_NAME.search(relative) or any(
            relative == declared or relative.endswith("/" + declared)
            for declared in required_notices or []
        ) for _, relative in files):
            raise RuntimeError(
                f"Missing license text: {ecosystem} {name}@{version}. Reinstall the exact upstream package with its notices, "
                f"or place its unmodified LICENSE/NOTICE files and SOURCE.json "
                f"({{'version': '{version}', 'source': 'https://upstream-source-for-this-version'}}) in {override}."
            )
        for required in required_notices or []:
            normalized = PurePosixPath(required).as_posix()
            if not any(relative == normalized or relative.endswith("/" + normalized) for _, relative in files):
                raise RuntimeError(
                    f"Missing declared notice {required}: {ecosystem} {name}@{version}. "
                    f"Restore it from that exact upstream version, or add its original path/text and SOURCE.json in {override}."
                )
        prefix = PurePosixPath(ecosystem, safe_name, safe_version)
        copied = []
        seen = set()
        for source, relative in files:
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"Unsafe notice path for {name}@{version}: {relative}")
            destination = self.output / prefix / path
            content = source.read_bytes()
            if not content.strip():
                raise RuntimeError(f"Empty license text: {name}@{version}: {source}")
            if destination.exists() and destination.read_bytes() != content:
                raise RuntimeError(f"Conflicting notices: {name}@{version}: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            key = destination.relative_to(self.output).as_posix()
            if key not in seen:
                copied.append({"path": key, "sha256": hashlib.sha256(content).hexdigest()})
                seen.add(key)
        self.components.append({"ecosystem": ecosystem, "name": name, "version": version,
                                "license": license_id, "sources": sources, "files": copied})

    def project(self) -> dict:
        project = tomllib.loads((self.root / "pyproject.toml").read_text())["project"]
        required = ["LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "LICENSES/Apache-2.0.txt", "desktop/LICENSE"]
        for name in required:
            if not (self.root / name).is_file():
                raise RuntimeError(f"Missing project license/notice: {name}")
        if (self.root / "TRADEMARK.md").is_file():
            required.append("TRADEMARK.md")
        self.add("project", project["name"], project["version"], project["license"],
                 ["https://github.com/LjyYano/link-read-desktop"], [(self.root / name, name) for name in required])
        vendor = self.root / "src/linkread_desktop/renderers/vendor"
        inventory = json.loads((self.desktop / "scripts/vendor_licenses.json").read_text())
        covered = set()
        for entry in inventory:
            for asset, expected in entry["assets"].items():
                actual = hashlib.sha256((vendor / asset).read_bytes()).hexdigest()
                if actual != expected:
                    raise RuntimeError(f"Vendored asset changed: {asset}; review its upstream version/notices and update scripts/vendor_licenses.json")
            notice = entry["notice"]
            self.add("vendor", entry["name"], entry["version"], entry["license"], [entry["source"]],
                     [(vendor / notice, notice)])
            self.components[-1]["bundled_assets"] = entry["assets"]
            covered.add(notice)
        if covered != {path.name for path in vendor.glob("*.LICENSE.txt")}:
            raise RuntimeError("Renderer notice inventory is incomplete; update scripts/vendor_licenses.json")
        return project

    def python(self, project: dict, extra_distributions: set[str] | list[str] | None = None) -> None:
        # Include the bootloader's license and exception, plus a conservative
        # superset of its build dependencies and frozen sidecar distributions.
        project_name = canonicalize_name(project["name"])
        pending = [Requirement(value) for value in project.get("dependencies", [])
                   + project.get("optional-dependencies", {}).get("youtube", []) + ["pyinstaller"]]
        for extra_name in sorted({canonicalize_name(item) for item in (extra_distributions or ())}):
            if extra_name != project_name:
                pending.append(Requirement(extra_name))
        visited: dict[str, set[str]] = {}
        distributions: dict[str, metadata.Distribution] = {}
        while pending:
            requirement = pending.pop()
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            name = canonicalize_name(requirement.name)
            extras = set(requirement.extras) | {""}
            try:
                distribution = metadata.distribution(name)
            except metadata.PackageNotFoundError as exc:
                raise RuntimeError(f"Missing build dependency {requirement}; install the project's youtube extras and PyInstaller") from exc
            if requirement.specifier and distribution.version not in requirement.specifier:
                raise RuntimeError(f"Installed {name}=={distribution.version} does not satisfy {requirement}")
            if name in visited and extras <= visited[name]:
                continue
            visited.setdefault(name, set()).update(extras)
            distributions[name] = distribution
            for value in distribution.requires or []:
                dependency = Requirement(value)
                if dependency.marker is None or any(dependency.marker.evaluate({"extra": extra}) for extra in visited[name]):
                    dependency.marker = None  # Already evaluated for the requesting package's extras.
                    pending.append(dependency)
        for name, distribution in sorted(distributions.items()):
            files = []
            for item in distribution.files or []:
                relative = PurePosixPath(str(item))
                if ".." in relative.parts or relative.is_absolute():
                    continue
                if relative.suffix.lower() not in CODE_SUFFIXES and (
                    NOTICE_NAME.match(relative.name)
                    or any(part.lower() in {"licenses", "licences"} for part in relative.parts[:-1])
                ):
                    source = Path(distribution.locate_file(item))
                    if source.is_file():
                        files.append((source, relative.as_posix()))
            info = distribution.metadata
            sources = info.get_all("Project-URL", []) + ([info["Home-page"]] if info.get("Home-page") else [])
            self.add("python", name, distribution.version, info.get("License-Expression") or info.get("License"),
                     sources, files, info.get_all("License-File", []))
        python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
        if not python_license.is_file():
            python_license = Path(sys.base_prefix) / "LICENSE.txt"
        self.add("runtime", "CPython", sys.version.split()[0], "PSF-2.0 and included notices",
                 ["https://www.python.org/"], [(python_license, "LICENSE.txt")] if python_license.is_file() else [])

    def npm(self) -> None:
        lock = json.loads((self.desktop / "package-lock.json").read_text())
        for relative, entry in sorted(lock["packages"].items()):
            if not relative or entry.get("dev"):
                continue
            directory = self.desktop / relative
            if not directory.is_dir() and entry.get("optional"):
                continue  # npm did not install this optional package for the build platform.
            manifest = directory / "package.json"
            if not manifest.is_file():
                raise RuntimeError(f"Missing npm dependency {relative}@{entry.get('version')}; run npm ci")
            package = json.loads(manifest.read_text())
            if package["version"] != entry["version"]:
                raise RuntimeError(f"npm lock mismatch: {package['name']} is {package['version']}, expected {entry['version']}; run npm ci")
            repository = package.get("repository", {})
            source = repository.get("url") if isinstance(repository, dict) else repository
            license_id = package.get("license")
            required = [license_id.removeprefix("SEE LICENSE IN ")] if isinstance(license_id, str) and license_id.startswith("SEE LICENSE IN ") else []
            self.add("npm", package["name"], package["version"], license_id,
                     [value for value in (source, entry.get("resolved")) if value],
                     [(path, path.relative_to(directory).as_posix()) for path in notice_files(directory)], required)

    def rust(self, target: str) -> None:
        if not shutil.which("cargo"):
            raise RuntimeError("cargo is required to collect the exact native dependency notices; install the target Rust toolchain before App packaging")
        result = subprocess.run(["cargo", "metadata", "--locked", "--format-version", "1", "--filter-platform", target],
                                cwd=self.desktop / "src-tauri", text=True, capture_output=True, check=True, timeout=300)
        graph = json.loads(result.stdout)
        selected = {node["id"] for node in graph["resolve"]["nodes"]} - set(graph["workspace_members"])
        for package in sorted(graph["packages"], key=lambda value: (value["name"], value["version"])):
            if package["id"] not in selected:
                continue
            directory = Path(package["manifest_path"]).parent
            files = [(path, path.relative_to(directory).as_posix()) for path in notice_files(directory)]
            declared = package.get("license_file")
            if declared:
                source = directory / declared
                if source.is_file():
                    files.append((source, "declared/" + source.name))
                else:
                    raise RuntimeError(f"Missing declared license file for Rust {package['name']}@{package['version']}: {source}")
            self.add("rust", package["name"], package["version"], package.get("license"),
                     [value for value in (package.get("repository"), package.get("source")) if value], files,
                     [Path(declared).name] if declared else [])

    def manifest(self, target: str, native_libraries: list[str] | None = None) -> dict:
        inputs = ["pyproject.toml", "desktop/package-lock.json", "desktop/src-tauri/Cargo.lock", "desktop/scripts/vendor_licenses.json"]
        data = {
            "schema_version": 1, "generator": "linkread-desktop/collect_licenses.py",
            "target": target, "python": sys.version.split()[0],
            "app_version": json.loads((self.desktop / "package.json").read_text())["version"],
            "scope": "Project notices, vendored reader assets, Python runtime/package and packager dependency notices, installed npm production dependencies, and target-resolved Rust dependency notices. Build dependencies may be included as a conservative superset. This inventory does not replace review of native libraries, source-delivery duties, or distribution terms.",
            "inputs": {name: hashlib.sha256((self.root / name).read_bytes()).hexdigest() for name in inputs},
            "components": self.components,
        }
        if native_libraries is not None:
            data["bundled_native_libraries"] = native_libraries
        return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Exact Rust target triple used by the App build")
    parser.add_argument("--output", type=Path, default=DESKTOP / "build/licenses")
    parser.add_argument("--sidecar-dir", type=Path, default=None, help="Bundled --onedir sidecar directory to audit")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.output.is_symlink():
        raise RuntimeError(f"The license output must not be a symlink: {args.output}")
    if args.sidecar_dir is not None and not args.sidecar_dir.is_dir():
        raise RuntimeError(f"Missing --sidecar-dir directory: {args.sidecar_dir}")
    if output.exists() and any(output.iterdir()):
        manifest = output / "manifest.json"
        if not manifest.is_file() or json.loads(manifest.read_text()).get("generator") != "linkread-desktop/collect_licenses.py":
            raise RuntimeError(f"Refusing to replace a non-generated, non-empty directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    extra_python = bundled_python_distributions(args.sidecar_dir) if args.sidecar_dir is not None else None
    native_libraries = bundled_native_libraries(args.sidecar_dir) if args.sidecar_dir is not None else None
    with tempfile.TemporaryDirectory(prefix="licenses-", dir=output.parent) as temporary:
        staging = Path(temporary) / "notices"
        staging.mkdir()
        collector = Collector(staging)
        project = collector.project()
        collector.python(project, extra_distributions=extra_python)
        collector.npm()
        collector.rust(args.target)
        (staging / "manifest.json").write_text(
            json.dumps(collector.manifest(args.target, native_libraries=native_libraries), ensure_ascii=False, indent=2) + "\n",
        )
        if output.exists():
            shutil.rmtree(output)
        shutil.move(staging, output)
    print(f"Collected original notices for {len(collector.components)} components into {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"License collection failed: {error}") from error
