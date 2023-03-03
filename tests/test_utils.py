import os
from pathlib import Path

from ops2deb.utils import working_directory


def test_working_directory__sets_current_working_directory_within_context(tmp_path):
    origin = Path().absolute()
    with working_directory(tmp_path):
        assert Path(os.getcwd()) != origin
        assert Path(os.getcwd()) == tmp_path


def test_working_directory__restores_current_directory_when_context_is_left(tmp_path):
    origin = Path().absolute()
    with working_directory(tmp_path):
        pass
    assert Path(os.getcwd()) == origin
