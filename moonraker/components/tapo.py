# Unofficial Tapo Switch component
#
# Copyright (C) 2025 Satoshi Ohba <satoshi.ohba@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# moonraker/components/power_tapo_switch.py
#
# using Mihai Dinculescu's Unofficial Tapo API Client.
# https://github.com/mihai-dinculescu/tapo
# https://pypi.org/project/tapo/
# Copyright (c) 2022-2025 Mihai Dinculescu

from __future__ import annotations
from typing import Any
import logging
import sys
import asyncio
from .power import PrinterPower
from .power import PowerDevice

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Type,
    Any,
    Optional,
    Dict
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper

class UnofficialPrinterPower:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        # retrieve existing, or create power component instance
        self.power = self.server.load_component(config, "power")
        self.unofficial_devices: Dict[str, PowerDevice] = {}
        prefix_sections = config.get_prefix_sections("tapo")
        logging.info(f"Unofficial power component loading devices: {prefix_sections}")
        dev_types = {
            "tplink_tapo": TPLinkTapo,
        }

        for section in prefix_sections:
            cfg = config[section]
            dev_type: str = cfg.get("type")
            dev_class: Optional[Type[PowerDevice]]
            dev_class = dev_types.get(dev_type)
            if dev_class is None:
                raise config.error(f"Unsupported Device Type: {dev_type}")
            try:
                dev = dev_class(cfg)
            except Exception as e:
                msg = f"Failed to load power device [{cfg.get_name()}]\n{e}"
                self.server.add_warning(msg, exc_info=e)
                continue
            self.unofficial_devices[dev.get_name()] = dev

    # power.add_device() must be called from async method
    async def component_init(self) -> None:
        for dev_name, device in self.unofficial_devices.items():
            await self.power.add_device(dev_name, device)

class TPLinkTapo(PowerDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config)

        if sys.version_info < (3, 11):
            raise config.error(
                f"[{config.get_name()}]: Tapo support skipped: requires Python 3.11+")

        try:
            import tapo
        except ImportError:
            raise config.error(
                f"[{config.get_name()}]: Import Error:"
                " Tapo power device support unavailable."
                " Install 'tapo' for support.\nexample:\n"
                "$ ~/moonraker-env/bin/pip install"
                " -r ~/moonraker/scripts/moonraker-tapo.txt\n"
                "$ sudo systemctl restart moonraker\n"
            )
        logging.info("Tapo power device support enabled.")

        self.addr: str = config.get("address")
        self.user: str = config.load_template("user").render()
        self.password: str = config.load_template("password").render()
        self.power_strip: bool = config.getboolean("power_strip", False)
        self.device_id: Optional[str] = config.get("device_id", None)
        self.nickname: Optional[str] = config.get("nickname", None)
        self.output_id: Optional[int] = config.getint("output_id", None)

        self._api_client: tapo.ApiClient = tapo.ApiClient(self.user, self.password)

        logging.info(
            f"Tapo SmartPlug initialized for {self.name} at {self.addr}"
            f" power_strip:{self.power_strip} device_id:{self.device_id}"
            f" nickname:{self.nickname} output_id:{self.output_id}"
        )

        if (self.power_strip
            and (self.device_id is None
                 and self.nickname is None
                 and self.output_id is None)):
            raise config.error(
                f"[{config.get_name()}]: Configuration Error:"
                " Missing required identifier."
                " If power_strip is true, one of device_id, nickname,"
                " or output_id must be defined."
            )

    async def _get_device(self) -> Any:
        try:
            if not self.power_strip:
                # Smart Plug
                plug_device = await self._api_client.generic_device(self.addr)
            else:
                # Smart Power Strip which has multiple plugs
                power_strip_device = await self._api_client.p300(self.addr)
                # device_id: Optional[str]
                #     The Device ID of the device. (40-digits hex str)
                # nickname:  Optional[str]
                #     The Nickname of the device. (User specified str with Tapo app.)
                # position: Optional[int]
                #     The Position of the device (Position Index)
                # Identify Priority: 1.device_id, 2.nickname, 3.position
                plug_device = await power_strip_device.plug(
                    device_id=self.device_id,
                    nickname=self.nickname,
                    position=self.output_id
                )
            return plug_device
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.state = "error"
            logging.exception(f"Error Get Tapo Device: {self.name}{e}")
            raise

    async def init_state(self) -> None:
        await self.refresh_status()

    async def refresh_status(self) -> None:
        try:
            device = await self._get_device()
            device_info = await device.get_device_info()
            device_on: bool = device_info.device_on
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.state = "error"
            if self._should_log_error():
                logging.exception(
                    f"Error Refreshing Device Status: {self.name} {e}"
                )
        else:
            self.state = "on" if device_on else "off"

    async def set_power(self, state) -> None:
        try:
            device = await self._get_device()
            if state == "on":
                await device.on()
            else:
                await device.off()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.state = "error"
            msg = f"Error Toggling Device Power: {self.name} {e}"
            if self._should_log_error():
                logging.exception(msg)
            raise self.server.error(msg)
        else:
            self.state = state

def load_component(config: ConfigHelper):
    return UnofficialPrinterPower(config)
