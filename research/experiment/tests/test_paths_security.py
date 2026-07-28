from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automatic_experiment.paths import (
    PathPolicyError,
    _reject_special,
    _scientific_profile,
    resolve_input_reference,
)
from automatic_experiment.state import task_workspace


class PathSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_workspace = tempfile.TemporaryDirectory(
            prefix="path_security_workspace_"
        )
        self.addCleanup(self._temporary_workspace.cleanup)
        workspace = Path(self._temporary_workspace.name)
        self.inputs = workspace / "inputs"
        self.inputs.mkdir(parents=True)
        (self.inputs / "example_mean.csv").write_text(
            "group,value\nA,1\nB,2\n",
            encoding="utf-8",
        )
        self._workspace_scope = task_workspace(workspace)
        self._workspace_scope.__enter__()
        self.addCleanup(self._workspace_scope.__exit__, None, None, None)
        self.temporary: list[Path] = []

    def tearDown(self) -> None:
        for path in reversed(self.temporary):
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

    def test_example_input_is_allowed(self) -> None:
        resolved = resolve_input_reference("inputs/example_mean.csv")
        self.assertTrue(resolved.is_file())

    def test_traversal_is_rejected(self) -> None:
        with self.assertRaises(PathPolicyError):
            resolve_input_reference("../README.md")

    def test_windows_absolute_path_is_rejected(self) -> None:
        with self.assertRaises(PathPolicyError):
            resolve_input_reference("C:/Users/example/data.csv")

    def test_backslash_path_is_rejected(self) -> None:
        with self.assertRaises(PathPolicyError):
            resolve_input_reference("inputs\\example_mean.csv")

    def test_protected_directory_is_rejected(self) -> None:
        protected = self.inputs / "private"
        protected.mkdir(exist_ok=True)
        self.temporary.append(protected)
        file_path = protected / "data.csv"
        file_path.write_text("x\n1\n", encoding="utf-8")
        self.temporary.append(file_path)
        with self.assertRaises(PathPolicyError):
            resolve_input_reference("inputs/private/data.csv")

    def test_secret_like_filename_is_rejected(self) -> None:
        secret = self.inputs / "api_key.txt"
        secret.write_text("not-a-real-secret", encoding="utf-8")
        self.temporary.append(secret)
        with self.assertRaises(PathPolicyError):
            resolve_input_reference("inputs/api_key.txt")

    def test_secret_like_content_is_rejected(self) -> None:
        secret = self.inputs / "ordinary-notes.txt"
        secret.write_text(
            "DASHSCOPE_API_KEY=fake_example_token_123456789",
            encoding="utf-8",
        )
        self.temporary.append(secret)
        with self.assertRaises(PathPolicyError):
            from automatic_experiment.paths import _reject_secret_content

            _reject_secret_content(secret)

    def test_hard_link_is_rejected(self) -> None:
        source = self.inputs / "hardlink-source.txt"
        link = self.inputs / "hardlink-copy.txt"
        source.write_text("data", encoding="utf-8")
        self.temporary.extend([link, source])
        os.link(source, link)
        with self.assertRaises(PathPolicyError):
            resolve_input_reference("inputs/hardlink-copy.txt")

    def test_symbolic_link_is_rejected_when_supported(self) -> None:
        link = self.inputs / "link.csv"
        try:
            link.symlink_to(self.inputs / "example_mean.csv")
        except OSError:
            self.skipTest("symbolic links require additional Windows privileges")
        self.temporary.append(link)
        with self.assertRaises(PathPolicyError):
            resolve_input_reference("inputs/link.csv")

    def test_junction_is_rejected_by_policy(self) -> None:
        if not hasattr(Path, "is_junction"):
            self.skipTest("Path.is_junction is unavailable")
        with patch.object(Path, "is_junction", return_value=True):
            with self.assertRaises(PathPolicyError):
                _reject_special(self.inputs)

    def _wsl_path(self, path: Path) -> str:
        resolved = path.resolve()
        return f"/mnt/{resolved.drive[0].lower()}{resolved.as_posix().split(':', 1)[1]}"

    def _make_scientific_fixture(self, path: Path, code: str) -> None:
        if os.name != "nt" or shutil.which("wsl.exe") is None:
            self.skipTest("scientific-container fixture generation requires WSL")
        self.temporary.append(path)
        completed = subprocess.run(
            [
                "wsl.exe",
                "-d",
                "Ubuntu-E",
                "--",
                "python3",
                "-c",
                code,
                self._wsl_path(path),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            self.skipTest(
                "configured WSL scientific runtime is unavailable: "
                + completed.stderr[:200]
            )

    def test_scientific_container_metadata_profiles_are_bounded(self) -> None:
        fixtures = [
            (
                "metadata-test.fits",
                "from astropy.io import fits;import numpy as np,sys;"
                "fits.PrimaryHDU(np.arange(6,dtype='float32').reshape(2,3),"
                "header=fits.Header({'BUNIT':'G','DATE-OBS':'2026-01-01'})).writeto(sys.argv[1])",
                "fits",
            ),
            (
                "metadata-test.nc",
                "import xarray as xr,numpy as np,sys;"
                "xr.Dataset({'temperature':(('time',),np.array([1.,2.]))},"
                "coords={'time':np.array([0,1])}).to_netcdf(sys.argv[1],engine='h5netcdf')",
                "netcdf",
            ),
            (
                "metadata-test.h5",
                "import h5py,numpy as np,sys;"
                "f=h5py.File(sys.argv[1],'w');d=f.create_dataset('signal',data=np.arange(4));"
                "d.attrs['unit']='nT';f.close()",
                "hdf5",
            ),
            (
                "metadata-test.parquet",
                "import pyarrow as pa,pyarrow.parquet as pq,sys;"
                "pq.write_table(pa.table({'value':[1.0,2.0]}),sys.argv[1])",
                "parquet",
            ),
        ]
        for filename, code, expected_format in fixtures:
            with self.subTest(format=expected_format):
                path = self.inputs / filename
                self._make_scientific_fixture(path, code)
                profile = _scientific_profile(path)
                self.assertIsNotNone(profile)
                self.assertTrue(profile["profile_complete"])
                self.assertEqual(profile["format"], expected_format)

    def test_damaged_scientific_container_is_reported_without_array_loading(self) -> None:
        path = self.inputs / "metadata-damaged.fits"
        path.write_bytes(b"not a FITS file")
        self.temporary.append(path)
        profile = _scientific_profile(path)
        self.assertFalse(profile["profile_complete"])
        self.assertIn("reason", profile)

    def test_scientific_container_size_limit_is_enforced_before_inspection(self) -> None:
        path = self.inputs / "metadata-oversized.parquet"
        with path.open("wb") as handle:
            handle.truncate(513 * 1024 * 1024)
        self.temporary.append(path)
        with self.assertRaisesRegex(PathPolicyError, "size limit"):
            _scientific_profile(path)

    def test_hdf5_external_link_is_rejected(self) -> None:
        path = self.inputs / "metadata-external.h5"
        self._make_scientific_fixture(
            path,
            "import h5py,sys;f=h5py.File(sys.argv[1],'w');"
            "f['external']=h5py.ExternalLink('/etc/passwd','/data');f.close()",
        )
        with self.assertRaisesRegex(PathPolicyError, "external links"):
            _scientific_profile(path)


if __name__ == "__main__":
    unittest.main()
