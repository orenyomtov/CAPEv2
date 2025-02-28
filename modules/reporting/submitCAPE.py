# encoding: utf-8
# Copyright (C) 2015 Kevin O'Reilly kevin.oreilly@contextis.co.uk
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging

from lib.cuckoo.common.abstracts import Report
from lib.cuckoo.common.cape_utils import submit_task
from lib.cuckoo.common.config import Config

log = logging.getLogger(__name__)

reporting_conf = Config("reporting")

NUMBER_OF_DEBUG_REGISTERS = 4
bp = 0

cape_package_list = [
    "Emotet",
    "Emotet_doc",
    "Unpacker",
    "Unpacker_dll",
    "Unpacker_regsvr",
    "Unpacker_zip",
    "Unpacker_ps1",
    "Unpacker_js",
    "Hancitor",
    "Hancitor_dll",
    "Hancitor_doc",
    "PlugX",
    "PlugXPayload",
    "PlugX_dll",
    "PlugX_doc",
    "PlugX_zip",
    "RegBinary",
    "Shellcode-Extraction",
    "TrickBot",
    "TrickBot_doc",
    "UPX",
    "UPX_dll",
]

unpackers = {
    "ps1": "Unpacker_ps1",
    "dll": "Unpacker_dll",
    "regsvr": "Unpacker_regsvr",
    "zip": "Unpacker_zip",
    "js": "Unpacker_js",
    "exe": "Unpacker",
}

plugx = {
    "PlugXPayload": "PlugXPayload",
    "zip": "PlugX_zip",
    "doc": "PlugX_doc",
    "dll": "PlugX_dll",
    "exe": "PlugX",
}


class SubmitCAPE(Report):
    def process_cape_yara(self, cape_yara, results, detections):
        global bp

        if "cape_options" in cape_yara["meta"]:
            cape_options = cape_yara["meta"]["cape_options"].split(",")

            address = 0
            new_options = ""
            suffix = ""
            for option in cape_options:
                name, value = option.split("=")
                if name in ("bp0", "br0", 0):
                    bp = 1
                elif name in ("bp1", "br1", 1):
                    bp = 2
                elif name in ("bp2", "br2", 2):
                    bp = 3
                elif name in ("bp3", "br3", 3):
                    bp = 4
                elif bp == NUMBER_OF_DEBUG_REGISTERS:
                    break
                elif name in ("bp", "br") and value.startswith("$"):
                    for hit in cape_yara["addresses"]:
                        pattern = False
                        if "-" in value:
                            pattern = "-"
                        elif "+" in value:
                            pattern = "+"

                        if pattern:
                            suffix = pattern + value.split(pattern, 2)[1]
                            value = value.split(pattern, 1)[0]

                        if value.strip("$") in hit and str(cape_yara["addresses"][hit]) not in self.task_options:
                            address = cape_yara["addresses"][hit]
                            option = f"{name}{bp}={address}{suffix}"
                            bp += 1
                if option not in self.task_options:
                    if new_options == "":
                        new_options = option
                    else:
                        new_options += f",{option}"

            if not address:
                return

            if "procdump=1" in self.task_options:
                self.task_options = self.task_options.replace("procdump=1", "procdump=0", 1)

            if "extraction=1" in self.task_options:
                self.task_options = self.task_options.replace("extraction=1", "extraction=0", 1)

            if "combo=1" in self.task_options:
                self.task_options = self.task_options.replace("combo=1", "combo=0", 1)

            if "file-offsets" in self.task_options:
                self.task_options = self.task_options.replace("file-offsets=0", "file-offsets=0", 1)
            else:
                self.task_options += ",file-offsets=1"

            log.info("options = %s", new_options)
            self.task_options += f",{new_options}"
            if "auto=" not in self.task_options:
                self.task_options += ",auto=1"

            return

        if "disable_cape=1" in self.task_options:
            return

        if cape_yara["name"] == "TrickBot":
            detections.add("TrickBot")

        if cape_yara["name"] == "Hancitor":
            detections.add("Hancitor")

    def run(self, results):
        self.task_options_stack = []
        self.task_options = None
        self.task_custom = None
        detections = set()
        children = []
        bp = 0

        # allow ban unittests
        filename = results.get("target", {}).get("file", {}).get("name", "")
        filenames = ("_test_00", "danabot")
        if any(fn in filename for fn in filenames):
            return
        # We only want to submit a single job if we have a
        # malware detection. A given package should do
        # everything we need for its respective family.
        package = None

        # allow custom extractors
        if reporting_conf.submitCAPE.keyword in results:
            return

        self.task_options = self.task["options"]
        if not self.task_options:
            return

        if "auto" in self.task_options:
            return

        # We want to suppress spawned jobs if a config
        # has already been extracted
        for entry in results.get("CAPE", []):
            if isinstance(entry, dict) and entry.get("cape_config"):
                return

        parent_package = results["info"].get("package")

        # Initial static hits from CAPE's yara signatures
        for entry in results.get("target", {}).get("file", {}).get("cape_yara", []):
            self.process_cape_yara(entry, results, detections)

        for pattern in ("procdump", "CAPE", "dropped"):
            for file in results.get(pattern, []) or []:
                if "cape_yara" in file:
                    for entry in file["cape_yara"]:
                        self.process_cape_yara(entry, results, detections)

        if "auto=1" in self.task_options:
            if parent_package and parent_package in unpackers.values():
                return

            parent_id = int(results["info"]["id"])
            if results.get("info", {}).get("options", {}).get("main_task_id", ""):
                parent_id = int(results.get("info", {}).get("options", {}).get("main_task_id", ""))

            self.task_custom = f"Parent_Task_ID:{results['info']['id']}"
            if results.get("info", {}).get("custom"):
                self.task_custom = f"{self.task_custom} Parent_Custom:{results['info']['custom']}"

            log.debug("submit_task options: %s", self.task_options)
            task_id = submit_task(
                self.task["target"],
                self.task["package"],
                self.task["timeout"],
                self.task_options,
                self.task["priority"] + 1,  # increase priority to expedite related submission
                self.task["machine"],
                self.task["platform"],
                self.task["memory"],
                self.task["enforce_timeout"],
                None,
                None,
                parent_id,
                self.task["tlp"],
                distributed=reporting_conf.submitCAPE.distributed,
                filename=filename,
                server_url=reporting_conf.submitCAPE.url or "",
            )
            if task_id:
                children = []
                children.append([task_id, self.task["package"]])
                results["CAPE_children"] = children

            return

        if "disable_cape=1" in self.task_options:
            return

        # Dynamic CAPE hits from packers
        if "signatures" in results:
            for entry in results["signatures"]:
                if parent_package:
                    if entry["name"] == "Unpacker":
                        if parent_package == "doc":
                            continue

                        if parent_package in unpackers:
                            detections.add(unpackers[parent_package])
                            continue

                    # Specific malware family packages
                    elif entry["name"] == "PlugX" and parent_package in plugx:
                        detections.add(plugx[parent_package])
                        package = plugx[parent_package]
                        continue

        elif "TrickBot" in detections:
            if parent_package == "doc":
                package = "TrickBot_doc"
            elif parent_package == "exe":
                package = "TrickBot"

        elif "Hancitor" in detections:
            if parent_package in ("doc"):
                package = "Hancitor_doc"
            elif parent_package in ("exe"):
                package = "Hancitor"
            elif parent_package in ("dll"):
                package = "Hancitor_dll"

        # if 'RegBinary' in detections or 'CreatesLargeKey' in detections:
        elif "RegBinary" in detections:
            package = "RegBinary"

        # we want to switch off automatic process dumps in CAPE submissions
        if self.task_options and "procdump=1" in self.task_options:
            self.task_options = self.task_options.replace("procdump=1", "procdump=0", 1)
        if self.task_options_stack:
            self.task_options = ",".join(self.task_options_stack)

        parent_id = int(results["info"]["id"])
        if results.get("info", {}).get("options", {}).get("main_task_id", ""):
            parent_id = int(results.get("info", {}).get("options", {}).get("main_task_id", ""))

        if package and package != parent_package:
            self.task_custom = f"Parent_Task_ID:{results['info']['id']}"
            if results.get("info", {}).get("custom"):
                self.task_custom = f"{self.task_custom} Parent_Custom:{results['info']['custom']}"
            task_id = self.submit_task(
                self.task["target"],
                package,
                self.task["timeout"],
                self.task_options,
                # increase priority to expedite related submission
                self.task["priority"] + 1,
                self.task["machine"],
                self.task["platform"],
                self.task["memory"],
                self.task["enforce_timeout"],
                None,
                None,
                parent_id,
                self.task["tlp"],
            )
            if task_id:
                children.append([task_id, package])

        else:  # nothing submitted, only 'dumpers' left
            if parent_package in cape_package_list:
                return

            self.task_custom = f"Parent_Task_ID:{results['info']['id']}"
            if results.get("info", {}).get("custom"):
                self.task_custom = f"{self.task_custom} Parent_Custom:{results['info']['custom']}"

            for dumper in detections:
                task_id = self.submit_task(
                    self.task["target"],
                    dumper,
                    self.task["timeout"],
                    self.task_options,
                    # increase priority to expedite related submission
                    self.task["priority"] + 1,
                    self.task["machine"],
                    self.task["platform"],
                    self.task["memory"],
                    self.task["enforce_timeout"],
                    None,
                    None,
                    parent_id,
                    self.task["tlp"],
                )
                if task_id:
                    children.append([task_id, dumper])

        if children:
            results["CAPE_children"] = children
