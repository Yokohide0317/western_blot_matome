"""Desktop launchers sharing one local server on macOS and Windows."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from wb import project as P, server
from wb.locks import file_lock

BUNDLE_ID = 'com.wbm.desktop'


def launcher_python():
    """Freeze an editable checkout into a local runtime outside protected Desktop.

    Normal uv tool installs already have a stable runtime and use it directly.
    A GUI app launched by LaunchServices must not depend on a checkout's venv.
    """
    from importlib.metadata import distribution
    import hashlib
    direct = distribution('wbm').read_text('direct_url.json')
    if not direct or not json.loads(direct).get('dir_info', {}).get('editable'):
        return sys.executable
    uv = shutil.which('uv') or str(Path.home() / '.local/bin' / ('uv.exe' if os.name == 'nt' else 'uv'))
    source = Path(__file__).resolve().parents[2]
    runtime = P.app_home() / 'desktop-runtime'
    python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    with file_lock(P.app_home() / 'desktop-runtime.lock'):
        with tempfile.TemporaryDirectory(prefix='wbm-runtime-') as temp:
            result = subprocess.run([uv, 'build', '--wheel', '--out-dir', temp], cwd=source,
                                    text=True, encoding='utf-8', capture_output=True, timeout=120)
            if result.returncode:
                raise ValueError('アプリ用パッケージを作成できませんでした: ' + result.stderr.strip())
            wheel = next(Path(temp).glob('*.whl'))
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            stamp = runtime / 'wbm-wheel.sha256'
            if python.exists() and stamp.exists() and stamp.read_text(encoding='utf-8') == digest:
                return str(python)
            if not python.exists():
                result = subprocess.run([uv, 'venv', '--python', sys._base_executable, str(runtime)],
                                        text=True, encoding='utf-8', capture_output=True, timeout=120)
                if result.returncode:
                    raise ValueError('アプリ用のPythonを用意できませんでした: ' + result.stderr.strip())
            result = subprocess.run([uv, 'pip', 'install', '--python', str(python), '--reinstall-package', 'wbm', str(wheel)],
                                    text=True, encoding='utf-8', capture_output=True, timeout=180)
            if result.returncode:
                raise ValueError('アプリ用の実行環境を用意できませんでした: ' + result.stderr.strip())
            stamp.write_text(digest, encoding='utf-8')
    return str(python)


def _applescript_string(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def app_script(open_browser=True):
    command = _applescript_string(shlex.join([launcher_python(), '-m', 'wb.desktop']))
    browse = f'    do shell script {command} & " browse --session " & quoted form of sessionID' if open_browser else ''
    show = f'    do shell script {command} & " show"' if open_browser else ''
    return f'''property sessionID : ""
property ownerPID : ""
property hubURL : ""

on launchServer()
    return do shell script {command} & " start --session " & quoted form of sessionID & " --owner-pid " & ownerPID
end launchServer

on showPage()
    set hubURL to my launchServer()
{show}
end showPage

on run
    set sessionID to do shell script "/usr/bin/uuidgen"
    set ownerPID to do shell script "echo $PPID"
    try
        set hubURL to my launchServer()
{browse}
    on error errorMessage
        display alert "WBMを起動できませんでした" message errorMessage as critical
        quit
    end try
end run

on reopen
    my showPage()
end reopen

on idle
    return 30
end idle

on quit
    if sessionID is not "" then
        try
            do shell script {command} & " stop --session " & quoted form of sessionID
        end try
    end if
    continue quit
end quit
'''


def default_app_path():
    override = os.environ.get('WBM_APPLICATIONS')
    if sys.platform == 'darwin':
        folder = Path(override) if override else Path('/Applications') if os.access('/Applications', os.W_OK) else Path.home() / 'Applications'
        return folder / 'WBM.app'
    if sys.platform == 'win32':
        folder = Path(override) if override else Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'Programs'
        return folder / 'WBM' / 'WBM.exe'
    raise ValueError('デスクトップアプリはmacOSとWindowsで利用できます')


def installed_app_path():
    try:
        path = Path(json.loads((P.app_home() / 'desktop-app.json').read_text(encoding='utf-8'))['path'])
        if path.is_absolute() and path.exists():
            return path
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return default_app_path()


def install_app(destination=None):
    owner = server.desktop_owner()
    if owner and server._alive(owner['owner_pid']):
        raise ValueError('WBMアプリを終了してから作成し直してください')
    automatic = destination is None
    destination = Path(destination or default_app_path()).expanduser().absolute()
    if automatic and sys.platform == 'darwin' and not destination.exists():
        legacy = Path.home() / 'Applications' / 'WBM.app'
        if legacy != destination and legacy.exists():
            try:
                with (legacy / 'Contents/Info.plist').open('rb') as stream:
                    owned = plistlib.load(stream).get('CFBundleIdentifier') == BUNDLE_ID
            except (OSError, ValueError):
                owned = False
            if owned:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy), str(destination))
    result = _install_mac(destination) if sys.platform == 'darwin' else _install_windows(destination) if sys.platform == 'win32' else None
    if result is None:
        raise ValueError('デスクトップアプリはmacOSとWindowsで利用できます')
    server._write_state({'path': str(destination)}, P.app_home() / 'desktop-app.json')
    return result


def _install_mac(destination):
    if destination.suffix != '.app':
        raise ValueError('保存先は.appで指定してください')
    if destination.exists():
        try:
            with (destination / 'Contents' / 'Info.plist').open('rb') as stream:
                existing = plistlib.load(stream)
        except (OSError, ValueError) as exc:
            raise ValueError('保存先に別のアプリがあります') from exc
        if existing.get('CFBundleIdentifier') != BUNDLE_ID:
            raise ValueError('保存先に別のアプリがあります')
        owner = server.desktop_owner()
        if owner and server._alive(owner['owner_pid']):
            raise ValueError('WBMアプリを終了してから作成し直してください')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.wbm-app-', dir=destination.parent) as folder:
        bundle = Path(folder) / 'WBM.app'
        result = subprocess.run(['/usr/bin/osacompile', '-s', '-o', str(bundle)], input=app_script(),
                                text=True, encoding='utf-8', capture_output=True, timeout=60)
        if result.returncode:
            raise ValueError('WBM.appを作成できませんでした: ' + result.stderr.strip())
        info = bundle / 'Contents' / 'Info.plist'
        with info.open('rb') as stream:
            metadata = plistlib.load(stream)
        from wb import __version__
        metadata.update(CFBundleIdentifier=BUNDLE_ID, CFBundleName='WBM', CFBundleDisplayName='WBM',
                        CFBundleShortVersionString=__version__, CFBundleVersion=__version__,
                        CFBundleIconFile='WBM.icns', NSHighResolutionCapable=True,
                        LSApplicationCategoryType='public.app-category.productivity')
        with info.open('wb') as stream:
            plistlib.dump(metadata, stream)
        from PIL import Image
        with Image.open(Path(__file__).parent / 'web' / 'hub' / 'icon.png') as icon:
            icon.save(bundle / 'Contents' / 'Resources' / metadata['CFBundleIconFile'], format='ICNS')
        signed = subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(bundle)],
                                text=True, encoding='utf-8', capture_output=True, timeout=60)
        if signed.returncode:
            raise ValueError('WBM.appの署名に失敗しました: ' + signed.stderr.strip())
        # Replace only our own previously installed bundle, with rollback on failure.
        previous = Path(folder) / 'previous.app'
        if destination.exists():
            destination.rename(previous)
        try:
            bundle.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
    return {'path': str(destination), 'message': 'WBM.appを作成しました。アプリの終了でサーバーも停止します。'}


def _install_windows(destination):
    if destination.name.lower() != 'wbm.exe':
        raise ValueError('保存先はWBM.exeで指定してください')
    folder = destination.parent
    manifest = folder / 'wbm-app.json'
    if folder.exists() and any(folder.iterdir()):
        try:
            owned = json.loads(manifest.read_text(encoding='utf-8')).get('bundle_id') == BUNDLE_ID
        except (OSError, ValueError):
            owned = False
        if not owned:
            raise ValueError('保存先に別のアプリがあります')
    folder.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix='.wbm-app-', dir=folder.parent) as temp:
        staging = Path(temp)
        icon = staging / 'WBM.ico'
        with Image.open(Path(__file__).parent / 'web/hub/icon.png') as image:
            image.save(icon, format='ICO')
        source = (Path(__file__).parent / 'windows_app.cs').read_text(encoding='utf-8')
        (staging / 'app.cs').write_text(source.replace('__PYTHON__', json.dumps(launcher_python())), encoding='utf-8-sig')
        script = '''$ErrorActionPreference = 'Stop'
$p = New-Object System.CodeDom.Compiler.CompilerParameters
$p.GenerateExecutable = $true
$p.OutputAssembly = Join-Path $PSScriptRoot 'WBM.exe'
$p.ReferencedAssemblies.AddRange(@('System.dll', 'System.Core.dll', 'System.Windows.Forms.dll', 'System.Drawing.dll'))
$p.CompilerOptions = '/target:winexe /win32icon:"' + (Join-Path $PSScriptRoot 'WBM.ico') + '"'
Add-Type -TypeDefinition (Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'app.cs')) -CompilerParameters $p
'''
        build = staging / 'build.ps1'
        build.write_text(script, encoding='utf-8-sig')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(build)],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not (staging / 'WBM.exe').is_file():
            raise ValueError('WBM.exeを作成できませんでした: ' + result.stderr.strip())
        folder.mkdir(parents=True, exist_ok=True)
        os.replace(staging / 'WBM.exe', destination)
        os.replace(icon, folder / 'WBM.ico')
        manifest.write_text(json.dumps({'bundle_id': BUNDLE_ID}), encoding='utf-8')
    if not os.environ.get('WBM_APPLICATIONS'):
        # A user-level Start menu shortcut; no administrator privileges.
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        link = Path(os.environ.get('APPDATA') or Path.home() / 'AppData/Roaming') / 'Microsoft/Windows/Start Menu/Programs/WBM.lnk'
        link.parent.mkdir(parents=True, exist_ok=True)
        script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({quote(link)}); "
                  f"$s.TargetPath={quote(destination)}; $s.WorkingDirectory={quote(folder)}; "
                  f"$s.IconLocation={quote(folder / 'WBM.ico')}; $s.Save()")
        import base64
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                        base64.b64encode(script.encode('utf-16le')).decode('ascii')], check=True, timeout=30,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return {'path': str(destination), 'message': 'WBM.exeを作成しました。アプリの終了でサーバーも停止します。'}


def _browse(url):
    if sys.platform == 'darwin':
        subprocess.Popen(['/usr/bin/open', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif sys.platform == 'win32':
        os.startfile(url)
    else:
        import webbrowser
        webbrowser.open(url)


def _browse_session(session=None):
    state = server._read_state()
    if not state or not server._is_ours(state):
        raise ValueError('WBMアプリの起動が完了していません')
    no_browser = False
    if session:
        request = P.app_home() / 'desktop-launch.json'
        try:
            preferences = json.loads(request.read_text(encoding='utf-8'))
            no_browser = time.time() - preferences['time'] < 60 and preferences.get('no_browser', False)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        request.unlink(missing_ok=True)
    if not no_browser:
        _browse(state['url'])
    return state['url']


def start_app(no_browser=False):
    # Explicit non-GUI operation for CI/remote shells, never set by the agent skill.
    if sys.platform not in ('darwin', 'win32') or os.environ.get('WBM_HEADLESS') == '1':
        return server.ensure_server()
    with file_lock(P.app_home() / 'desktop-launch.lock'):
        active = server.active_desktop_owner()
        if active:
            state = server._read_state()
            if not no_browser:
                _browse(state['url'])
            return {**state, 'app_pid': active['owner_pid'], 'app_path': str(installed_app_path())}
        path = installed_app_path()
        if not path.exists():
            install_app(path)
        server._write_state({'time': time.time(), 'no_browser': no_browser}, P.app_home() / 'desktop-launch.json')
        if sys.platform == 'darwin':
            command = ['/usr/bin/open', '-g']
            # LaunchServices does not inherit a terminal's environment by default.
            for key in ('WBM_HOME', 'WBM_CACHE', 'WBM_CONFIG', 'WBM_LIBRARY', 'WBM_APPLICATIONS'):
                value = str(P.app_home()) if key == 'WBM_HOME' else os.environ.get(key)
                if value is not None:
                    command.extend(['--env', key + '=' + value])
            result = subprocess.run([*command, str(path)], capture_output=True, text=True, encoding='utf-8', timeout=15)
            if result.returncode:
                raise ValueError('WBMアプリを開けませんでした: ' + result.stderr.strip())
        else:
            subprocess.Popen([str(path)], cwd=path.parent, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            owner = server.active_desktop_owner()
            if owner:
                return {**server._read_state(), 'app_pid': owner['owner_pid'], 'app_path': str(path)}
            time.sleep(.1)
        raise ValueError('WBMアプリの起動確認が時間切れになりました')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'browse', 'show'))
    parser.add_argument('--session')
    parser.add_argument('--owner-pid', type=int)
    args = parser.parse_args(argv)
    try:
        if args.action == 'start':
            if not args.owner_pid or args.owner_pid <= 1 or not server._alive(args.owner_pid):
                raise ValueError('WBMアプリは終了しています')
            previous = server._read_state()
            if previous and server._is_ours(previous):
                identity = server._identity(previous['port']) or {}
                if 'desktop_lifetime' not in identity.get('capabilities', []):
                    server.stop_server(expected_token=previous['token'])
            state = server.ensure_server(desktop={'session': args.session, 'owner_pid': args.owner_pid})
            print(state['url'])
        elif args.action == 'stop':
            if not args.session:
                raise ValueError('アプリのセッションがありません')
            server.stop_server(desktop_session=args.session)
        else:
            print(_browse_session(args.session if args.action == 'browse' else None))
        return 0
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
