# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The mock OpenStack cloud used by the tests. It lives in the package so demo mode can use it too."""
from openmove.demo.mock_cloud import (PASSWORD, PROJECT, TOKEN, MockCloud, demo_cloud, iso,  # noqa: F401
                                      sample_cloud, snapshot, volume)
