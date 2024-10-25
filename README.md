# Rako
Home Assistant Integration for [Rako Controls](https://rakocontrols.com)

> [!IMPORTANT]
> This integration works with Rako hubs like [RK-HUB](https://rakocontrols.com/rkhub/) or [WK-HUB](https://rakocontrols.com/wkhub/).

# Installation

When using [HACS](https://hacs.xyz/), select `HACS`, search for the `rako` integration, select it, and press the `Download` button to install to download the integration. Now continue the installation as described at [Using config flow](https://github.com/princekama/hacs-rako/blob/master/readme.md#using-config-flow)

You can install the code manually by copying the `rako` folder and all of its contents into your Home Assistant's `custom_components` folder. This is often located inside of your `/config` folder. If you are running Hass.io, use SAMBA to copy the folder over. If you are running Home Assistant Supervised, the `custom_components` folder might be located at `/usr/share/hassio/homeassistant`. It is possible that your `custom_components` folder does not exist. If that is the case, create the folder in the proper location, and then copy the `rako` folder and all of its contents inside the newly created `custom_components` folder. Then you have to restart Home Assistant for the component to be loaded properly.

# Using config flow

Within Home Assistant go to Settings - Devices & Services and pressing the `ADD INTEGRATION` button to create a new Integration, select `Rako` in the drop-down menu. Then enter a user defined client name (for example `home_assistant_rako`), enter the `Host` address of your Rako hub, and finish by pressing the `Submit` button.
