"""User settings, read from ``config.toml`` in ``project.app_home()`` (or ``$WBM_CONFIG``).

``[naming]``: how ChemiDoc export names are read, because they differ between
labs. It applies when images are imported (``wbm init`` / opening a new ZIP or
folder); a project already imported keeps what it read.
``[update]``: whether to look for a new release on GitHub once a day.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tomllib

from wb import project as P

DEFAULT_PATTERN = r"(?P<stem>.*?(?P<exposure>\d+(?:\.\d+)?)s)\((?P<mode>[^()]+)\)$"

TEMPLATE = '''# Western Blot Matome の設定
# 変更後は、新しく取り込むデータから反映されます（取り込み済みは wbm init <フォルダ> --refresh）。
# 書き方の確認: wbm config --check <ZIPまたはフォルダ>

[naming]
# 画像ファイル名（拡張子と raw16_suffix を除いた部分）から露光時間とモードを読む正規表現。
#   exposure : 露光時間の数値（必須）
#   mode     : 撮影モードの名前（必須）。下の signal_modes / marker_modes と照合します
#   stem     : 対応する .scn ファイルの名前（任意。無ければ .scn とは対応づけません）
# 既定値は ChemiDoc 標準の「… 0.200s(Colorimetric).tif」の形です。
pattern = '(?P<stem>.*?(?P<exposure>\\d+(?:\\.\\d+)?)s)\\((?P<mode>[^()]+)\\)$'

# 露光時間の単位を秒に直す倍率（ミリ秒で書かれているなら 0.001）
exposure_scale = 1.0

# バンドを撮った発光画像のモード名（大文字・小文字は区別しません）
signal_modes = ["Chemiluminescence"]

# マーカー（ラダー）を撮った画像のモード名
marker_modes = ["Colorimetric"]

# 16-bit 原画像の目印（拡張子の直前）。「… (Chemiluminescence).raw16.tif」なら ".raw16"
raw16_suffix = ".raw16"

[update]
# 1日1回、GitHub で新しいバージョンを確認する（false で確認しない）
check = true
'''


@dataclass(frozen=True)
class Naming:
    pattern: re.Pattern
    exposure_scale: float
    signal_modes: tuple[str, ...]
    marker_modes: tuple[str, ...]
    raw16_suffix: str

    def is_raw16(self, name: str) -> bool:
        lower = name.lower()
        return bool(self.raw16_suffix) and lower.endswith((self.raw16_suffix.lower() + ".tif", self.raw16_suffix.lower() + ".tiff"))

    def base(self, name: str) -> str:
        """File name without .tif/.tiff and without the raw16 marker."""
        stem = re.sub(r"\.tiff?$", "", name, flags=re.I)
        if self.raw16_suffix and stem.lower().endswith(self.raw16_suffix.lower()):
            stem = stem[: -len(self.raw16_suffix)]
        return stem

    def raw16_name(self, name: str) -> str:
        return self.base(name) + self.raw16_suffix + ".tif"

    def parse(self, name: str) -> dict | None:
        """``{'exposure_s', 'modality', 'mode', 'scn_name'}`` for a recognised image name, else None.

        ``modality`` is ``chemiluminescence`` (signal) or ``colorimetric`` (marker).
        """
        match = self.pattern.search(self.base(name))
        if not match:
            return None
        mode = match.group("mode").strip()
        if mode.lower() in self.signal_modes:
            modality = "chemiluminescence"
        elif mode.lower() in self.marker_modes:
            modality = "colorimetric"
        else:
            return None
        try:
            exposure = float(match.group("exposure")) * self.exposure_scale
        except ValueError:
            return None
        stem = match.groupdict().get("stem")
        return {"exposure_s": exposure, "modality": modality, "mode": mode,
                "scn_name": stem + ".scn" if stem else None}


def config_path() -> Path:
    return Path(os.environ["WBM_CONFIG"]).expanduser() if os.environ.get("WBM_CONFIG") else P.app_home() / "config.toml"


def _settings() -> dict:
    defaults = tomllib.loads(TEMPLATE)
    path = config_path()
    if not path.is_file():
        return defaults
    try:
        user = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"設定ファイルを読めません: {path}: {exc}") from exc
    unknown = set(user) - set(defaults)
    if unknown:
        raise ValueError(f"設定ファイルに不明な項目があります: {', '.join(sorted(unknown))}（{path}）")
    for section, values in defaults.items():
        if not isinstance(user.get(section, {}), dict):
            raise ValueError(f"{section} は [{section}] の表として書いてください（{path}）")
        extra = set(user.get(section, {})) - set(values)
        if extra:
            raise ValueError(f"[{section}] に不明な項目があります: {', '.join(sorted(extra))}（{path}）")
    return {section: {**values, **user.get(section, {})} for section, values in defaults.items()}


def naming() -> Naming:
    values = _settings()["naming"]
    try:
        pattern = re.compile(values["pattern"], re.I)
    except re.error as exc:
        raise ValueError(f"naming.pattern の正規表現が正しくありません: {exc}") from exc
    missing = {"exposure", "mode"} - set(pattern.groupindex)
    if missing:
        raise ValueError("naming.pattern には (?P<exposure>…) と (?P<mode>…) が必要です")
    modes = lambda key: tuple(str(m).strip().lower() for m in values[key])
    signal, marker = modes("signal_modes"), modes("marker_modes")
    if not signal or not marker or set(signal) & set(marker):
        raise ValueError("signal_modes と marker_modes は、どちらも1つ以上で、重ならないようにしてください")
    scale = float(values["exposure_scale"])
    if not scale > 0:
        raise ValueError("exposure_scale は正の数にしてください")
    return Naming(pattern, scale, signal, marker, str(values["raw16_suffix"]))


def update_check() -> bool:
    """``[update] check``: look for a new release on GitHub (at most once a day)."""
    value = _settings()["update"]["check"]
    if not isinstance(value, bool):
        raise ValueError("[update] check は true か false にしてください")
    return value


def show() -> dict:
    path = config_path()
    settings = _settings()
    return {"path": str(path), "exists": path.is_file(), "naming": settings["naming"], "update": settings["update"]}


def init() -> dict:
    """Write the commented template unless a config file already exists."""
    path = config_path()
    created = not path.exists()
    if created:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE, encoding="utf-8")
    return {"path": str(path), "created": created}


def check(target) -> dict:
    """How each image name in a ZIP or folder is read with the current settings (no image is opened)."""
    import zipfile

    rules = naming()
    source = Path(target).expanduser()
    if source.suffix.lower() == ".zip":
        P.require_local([source])
        try:
            with zipfile.ZipFile(source) as archive:
                names = [n for n in archive.namelist() if not n.endswith("/")]
        except zipfile.BadZipFile as exc:
            raise ValueError(f"ZIPとして読めません: {source.name}") from exc
    elif source.is_dir():
        names = [str(p.relative_to(source)) for p in source.rglob("*") if p.is_file()]
    else:
        raise ValueError(f"ZIPか画像フォルダを指定してください: {source}")
    names = [n for n in names if "__MACOSX" not in n and not Path(n).name.startswith("._")
             and "wblot_output" not in Path(n).parts]
    files = []
    for name in sorted(n for n in names if re.search(r"\.tiff?$", n, re.I)):
        parsed = rules.parse(Path(name).name)
        files.append({
            "file": name,
            "kind": "raw16" if rules.is_raw16(Path(name).name) else "tiff",
            "modality": parsed["modality"] if parsed else None,
            "exposure_s": parsed["exposure_s"] if parsed else None,
        })
    recognised = [f for f in files if f["modality"]]
    return {
        "config": str(config_path()),
        "images": len(files),
        "signal": sum(f["modality"] == "chemiluminescence" for f in recognised),
        "marker": sum(f["modality"] == "colorimetric" for f in recognised),
        "unrecognised": [f["file"] for f in files if not f["modality"]],
        "files": files,
    }
