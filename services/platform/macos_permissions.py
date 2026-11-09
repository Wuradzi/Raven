"""Read-only macOS privacy-permission probes (no prompts, no recording, no secrets).

Each probe returns 'granted', 'denied', 'not_determined' or 'unknown'. 'unknown' means the
needed pyobjc framework is missing or the call is unavailable, never a guess of 'granted'.
"""
import importlib

SETTINGS_PANES = {
    'microphone': 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone',
    'accessibility': 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility',
    'screen_recording': 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture',
}
_AV_STATUS = {0: 'not_determined', 1: 'denied', 2: 'denied', 3: 'granted'}


def _framework(name):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def accessibility_status():
    module = _framework('ApplicationServices')
    check = getattr(module, 'AXIsProcessTrusted', None)
    if check is None:
        return 'unknown'
    return 'granted' if check() else 'denied'


def screen_recording_status():
    module = _framework('Quartz')
    check = getattr(module, 'CGPreflightScreenCaptureAccess', None)
    if check is None:
        return 'unknown'
    return 'granted' if check() else 'denied'


def microphone_status():
    module = _framework('AVFoundation')
    device = getattr(module, 'AVCaptureDevice', None)
    if device is None:
        return 'unknown'
    # 'soun' is AVMediaTypeAudio; reading the status never shows the permission prompt.
    return _AV_STATUS.get(int(device.authorizationStatusForMediaType_('soun')), 'unknown')


def report():
    """Permission name -> status for the app that hosts Raven (Terminal, iTerm, IDE...)."""
    return {'microphone': microphone_status(), 'accessibility': accessibility_status(),
            'screen_recording': screen_recording_status()}
