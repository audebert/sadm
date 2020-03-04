# -*- encoding: utf-8 -*-
# This file is part of Prologin-SADM.
#
# Copyright (c) 2020 Rémi Audebert <remi.audebert@prologin.org>
#
# Prologin-SADM is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Prologin-SADM is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Prologin-SADM.  If not, see <http://www.gnu.org/licenses/>.

import asyncio
import logging
import optparse
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "prologin.concours.settings")

import django

django.setup()

import prologin.config
import prologin.log

from .matchmaker import MatchMaker

if __name__ == "__main__":
    # Argument parsing
    parser = optparse.OptionParser()
    parser.add_option(
        "-l",
        "--local-logging",
        action="store_true",
        dest="local_logging",
        default=False,
        help="Activate logging to stdout.",
    )
    parser.add_option(
        "-v",
        "--verbose",
        action="store_true",
        dest="verbose",
        default=False,
        help="Verbose mode.",
    )
    options, args = parser.parse_args()

    # Config
    config = prologin.config.load("matchmaker")

    # RPC Service
    s = MatchMaker(
        config=config,
        app_name="matchmaker",
        secret=config["matchmaker"]["shared_secret"].encode("utf-8"),
    )

    # Logging
    prologin.log.setup_logging(
        "matchmaker", verbose=options.verbose, local=options.local_logging
    )
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.server").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.web").setLevel(logging.WARNING)

    try:
        s.run()
    except KeyboardInterrupt:
        pass
