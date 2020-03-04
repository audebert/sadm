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
import dataclasses
import logging
import random
import itertools
from collections import defaultdict, Counter
from typing import List

import django.contrib.auth
from django.conf import settings as django_settings
from django.db.models import Max

import prologin.rpc.server
from prologin.concours.stechec.models import (
    Champion,
    Map,
    Match,
    MatchPriority,
    Tournament,
)


@dataclasses.dataclass
class MatchItem:
    '''Class that represents a MatchMaker match.'''

    champions: List[Champion]
    map: Map = None
    cancelled: bool = False

    @classmethod
    def from_db(kls, match_item_db: Match):
        return kls(
            champions=tuple(match_item_db.players.all()), map=match_item_db.map
        )

    def __hash__(self):
        return hash((self.champions, self.map))

    def as_bulk_create(self, author, tournament: Tournament):
        return {
            'author': author,
            'tournament': tournament,
            'champions': self.champions,
            **({'map': self.map} if self.map else {}),
        }


class MatchMaker(prologin.rpc.server.BaseRPCApp):
    """MatchMaker manages the matches of a tournament."""

    def __init__(self, *args, config=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = config
        # Current champions
        self.champions = set()
        # Matches managed by MatchMaker
        self.matches_with_champion = defaultdict(list)
        # The MatchMaker concours user
        self.author = None
        # The MatchMaker-managed tournament
        self.tournament = None
        # Tournament maps
        self.maps = []
        # The matches that are going to be scheduled
        # TODO: use collections.dequeue
        self.next_matches = []

    def run(self):
        logging.info(
            "matchmaker listening on port %s",
            self.config["matchmaker"]["port"],
        )

        self.run_task = asyncio.Task(self.run_task())

        super().run(port=self.config["matchmaker"]["port"])

    async def run_task(self):
        await self.setup()
        await self.bootstrap()

        self.dbwatcher = asyncio.Task(self.dbwatcher_task())
        self.match_creator = asyncio.Task(self.match_creator_task())

    def get_champions_query(self):
        all_chs = Champion.objects.filter(status="ready", deleted=False)

        # TODO
        deadline = None
        staff = True

        if deadline:
            all_chs = all_chs.filter(ts__lte=deadline)
        if not staff:
            all_chs = all_chs.filter(author__is_staff=False)

        # Last champion of each user
        # https://stackoverflow.com/questions/16074498
        chs_ids = (
            all_chs.values("author__id")
            .annotate(max_id=Max("id"))
            .values("max_id")
        )

        return Champion.objects.filter(pk__in=chs_ids)

    async def setup(self):
        self.author = django.contrib.auth.get_user_model().objects.get(
            pk=self.config["matchmaker"]["author_id"]
        )
        logging.info("setup: match author: %s", self.author)

        self.tournament, _ = Tournament.objects.get_or_create(
            name=self.config["matchmaker"]["tournament_name"],
            defaults={"author": self.author},
        )
        logging.info("setup: tournament: %s", self.tournament)

        self.maps = list(self.tournament.maps.all())
        logging.info("setup: tournament maps: %s", self.maps)

    async def bootstrap(self):
        # Get current champions
        current_champions = self.get_champions_query()
        self.champions = set(current_champions)
        logging.info("bootstrap: champions: %d", len(self.champions))

        # Remove old champions
        old_champions = self.tournament.players.exclude(
            pk__in=current_champions
        ).all()
        logging.info(
            "bootstrap: removing %d old champion(s) from tournament",
            len(old_champions),
        )
        self.tournament.players.remove(*old_champions)

        # Add new champions
        previous_champions = self.tournament.players.all()
        new_champions = [
            c for c in self.champions if c not in previous_champions
        ]
        logging.info(
            "bootstrap: adding %d new champion(s) to the tournament",
            len(new_champions),
        )
        self.tournament.players.add(*new_champions)

        # Remove old matches
        await self.cancel_matches(old_champions, self.champions)

        # Create missing matches
        logging.debug('bootstrap: computing missing matches')
        desired_matches = Counter(self.get_desired_matches(self.champions))
        current_matches = (
            MatchItem.from_db(m)
            for m in self.tournament.matches.all().prefetch_related('players')
        )
        desired_matches.subtract(current_matches)
        missing_matches = list(desired_matches.elements())
        logging.info(
            'bootstrap: creating %d missing matches', len(missing_matches)
        )
        random.shuffle(missing_matches)
        self.add_matches(missing_matches)

    def get_desired_matches(self, champions):
        """Yields all expected matches for champions."""
        for chs in itertools.product(
            champions, repeat=django_settings.STECHEC_NPLAYERS
        ):
            # Don't fight against yourself
            ch_ids = [c.id for c in chs]
            if len(set(ch_ids)) != len(ch_ids):
                continue
            yield from self.get_matches_with(chs)

    def get_matches_with(self, champions):
        match = MatchItem(champions=champions)
        for r in range(self.config["matchmaker"]["repeat"]):
            if django_settings.STECHEC_USE_MAPS:
                for map in self.maps:
                    yield dataclasses.replace(match, map=map)
            else:
                yield match

    def add_matches(self, matches):
        for match in matches:
            # Keep track of matches for each champion
            for champion in match.champions:
                self.matches_with_champion[champion].append(match)
        # Commit by replacing old match list to avoid race conditions
        self.next_matches = self.next_matches + matches

    async def cancel_matches(self, old_champions, current_champions):
        logging.info('Cancelling matches by champions %s', old_champions)

        # Cancel scheduled matches
        for old_champion in old_champions:
            for match in self.matches_with_champion[old_champion]:
                logging.debug('Cancelling %s', match)
                match.cancelled = True
            del self.matches_with_champion[old_champion]

        # Cancel already created matches
        cancelled_matches = self.tournament.matches.exclude(
            players__in=current_champions
        )
        logging.debug('Cancelling %d created matches', len(cancelled_matches))
        cancelled_matches.update(status='cancelled')
        for match in cancelled_matches.all():
            match.remove(self.tournament)

    async def schedule_new_matches(self, known_champions, new_champions):
        logging.info('Scheduling matches for new champions %s', new_champions)

        new_matches = []
        # Iterate over new matches
        for chs in itertools.product(
            new_champions | known_champions,
            repeat=django_settings.STECHEC_NPLAYERS,
        ):
            if not new_champions & set(chs):
                # No now champion in match
                continue

            # Don't fight against yourself
            ch_ids = [c.id for c in chs]
            if len(set(ch_ids)) != len(ch_ids):
                continue

            new_matches.extend(self.get_matches_with(chs))

        random.shuffle(new_matches)
        self.add_matches(new_matches)

    async def dbwatcher_task(self):
        """Watches for new champions."""
        while True:
            # Query database
            current_champions = set(self.get_champions_query())

            old_champions = self.champions - current_champions
            if old_champions:
                await self.cancel_matches(old_champions, current_champions)

            # Schedule new matches
            known_champions = self.champions & current_champions
            new_champions = current_champions - self.champions
            if new_champions:
                await self.schedule_new_matches(known_champions, new_champions)

            # Commit set of current champions
            self.champions = current_champions

            await asyncio.sleep(1)

    async def match_creator_task(self):
        """Create new matches at predefined rate per second."""
        while True:
            if self.next_matches:
                logging.info('%d matches in queue', len(self.next_matches))

            new_matches = []
            while (
                len(new_matches)
                < self.config["matchmaker"]["matches_per_second"]
                and len(self.next_matches) != 0
            ):
                # Peek first
                new_match = self.next_matches[0]
                # Drop first
                self.next_matches = self.next_matches[1:]
                if new_match.cancelled:
                    logging.debug('Skipping cancelled match: %s', new_match)
                    continue
                new_matches.append(
                    new_match.as_bulk_create(self.author, self.tournament)
                )

            if new_matches:
                logging.info('Creating %d matches', len(new_matches))
                Match.launch_bulk(
                    new_matches, priority=MatchPriority.TOURNAMENT
                )

            # TODO: replace with a condition on new matches
            await asyncio.sleep(1)
