from platformio.public import UnityTestRunner


class CustomTestRunner(UnityTestRunner):
    # Use project-local lib/Unity; skip PlatformIO Registry download
    EXTRA_LIB_DEPS = None
