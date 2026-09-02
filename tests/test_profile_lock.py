from pathlib import Path

from browser import PROFILE_LOCK_FILES, prepare_user_data_dir


def test_prepare_user_data_dir_removes_lock_files(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    lock = profile / "SingletonLock"
    lock.write_text("held")
    (profile / "DevToolsActivePort").write_text("9222")
    prepare_user_data_dir(profile, kill=False)
    for name in ("SingletonLock", "DevToolsActivePort"):
        assert not (profile / name).exists()
    for name in PROFILE_LOCK_FILES:
        assert name  # names stay defined
