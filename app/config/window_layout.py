"""Central policy for main-window placement across displays and DPI settings."""

# The first visible window uses the current screen's available work area rather
# than fixed pixels. Qt already reports device-independent coordinates, so this
# remains proportional on Windows scaling, remote desktop, and mixed-DPI screens.
FIRST_START_WORK_AREA_FRACTION = 0.80

# Increment only when the meaning of persisted window geometry changes. Legacy
# geometry came from an unconditional-maximize policy and must not be mistaken
# for an explicit user preference after this migration.
WINDOW_GEOMETRY_POLICY_VERSION = 2
WINDOW_GEOMETRY_POLICY_KEY = "window/geometry_policy_version"
