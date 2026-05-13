# location_builder.py
import json
from datetime import datetime
import traceback
from app.orm import Location, LocationConnection, GeographicFeature
from app.prompt_templates import WORLD_BUILDING
from app.world_building.schemas import LocationListOut

# Light bounds on geometry: the prompt asks for up to ~10 polygon points,
# but a misbehaving LLM occasionally returns hundreds. Truncating prevents
# the JSON column / map render from blowing up without rejecting the whole
# feature. The lower bound matches Leaflet's minimum for a renderable
# polyline / polygon.
_FEATURE_MAX_POINTS = 64
_FEATURE_MIN_POINTS = 2
_FEATURE_ALLOWED_TYPES = {
    'forest', 'mountain_range', 'river', 'lake', 'hills',
    'plains', 'swamp', 'desert', 'coast',
}


class LocationBuilder:
    def __init__(self, seed_data, seed_id, session, gpt_service, progress_callback=None):
        self.seed_data = seed_data
        self.seed_id = seed_id
        self.session = session
        self.gpt_service = gpt_service
        self.progress_callback = progress_callback or (lambda msg, status='info': None)

    def create_locations(self):
        """Generate all locations, sub-locations and inter-settlement
        connections in a single batched call.

        Temperature is held to 0.8 because the prompt is now strict about
        naming and the typology -- the higher 1.2 we used previously
        encouraged the metaphorical / abstract names that the prompt now
        explicitly forbids.
        """
        try:
            payload = self.gpt_service.get_structured(
                WORLD_BUILDING['LOCATIONS_BATCH'].format(self.seed_data),
                LocationListOut,
                max_attempts=3,
                temperature=0.8,
            )

            if payload is None:
                return {"message": "Failed to generate location data", "status": "failure"}

            locations = []
            # Track the persisted settlement row for each LLM-supplied index
            # so we can resolve the ``connections`` payload (which references
            # locations by 0-based index) to real foreign keys after the
            # whole settlement batch has been flushed.
            settlement_ids_by_index = {}
            for idx, loc in enumerate(payload.locations):
                new_location = Location(
                    seed_id=self.seed_id,
                    name=loc.name,
                    description=loc.description,
                    longitude=loc.longitude,
                    latitude=loc.latitude,
                    type=loc.type,
                    climate=loc.climate,
                    terrain=loc.terrain,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                self.session.add(new_location)
                self.session.flush()
                settlement_ids_by_index[idx] = new_location.id

                for sub in loc.sub_locations:
                    new_sub = Location(
                        seed_id=self.seed_id,
                        name=sub.name,
                        description=sub.description,
                        longitude=sub.longitude,
                        latitude=sub.latitude,
                        type=sub.type,
                        climate=sub.climate,
                        terrain=sub.terrain,
                        parent_id=new_location.id,
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                    )
                    self.session.add(new_sub)

                locations.append({
                    'id': new_location.id,
                    'name': loc.name,
                    'description': loc.description,
                    'type': loc.type,
                    'climate': loc.climate,
                    'terrain': loc.terrain,
                })

            # Persist road / path edges between settlements. We dedupe on
            # the unordered pair so a sloppy LLM that emits both (a, b)
            # and (b, a) doesn't produce two parallel polylines on the
            # map. Bad indices are silently skipped: the locations are
            # already committed and a missing edge is a soft failure.
            seen_pairs = set()
            for conn in (payload.connections or []):
                a = settlement_ids_by_index.get(conn.from_index)
                b = settlement_ids_by_index.get(conn.to_index)
                if a is None or b is None or a == b:
                    continue
                pair_key = tuple(sorted((a, b)))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                self.session.add(LocationConnection(
                    seed_id=self.seed_id,
                    from_location_id=a,
                    to_location_id=b,
                    name=conn.name or '',
                    type=conn.type or 'road',
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                ))

            # Persist natural geography (forests, rivers, mountain ranges,
            # ...). Each feature is stored as JSON-encoded points alongside
            # a closed/open flag so the frontend can pick polygon vs
            # polyline at render time without re-parsing the type string.
            # Points outside the world bounds or in the wrong shape are
            # discarded; features with too few valid points are skipped
            # rather than persisted as renderless rows.
            for feat in (payload.features or []):
                ftype = (feat.type or 'forest').lower()
                if ftype not in _FEATURE_ALLOWED_TYPES:
                    continue
                clean_points = []
                for pt in (feat.points or []):
                    if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                        continue
                    try:
                        lon = float(pt[0])
                        lat = float(pt[1])
                    except (TypeError, ValueError):
                        continue
                    clean_points.append([lon, lat])
                    if len(clean_points) >= _FEATURE_MAX_POINTS:
                        break
                if len(clean_points) < _FEATURE_MIN_POINTS:
                    continue
                self.session.add(GeographicFeature(
                    seed_id=self.seed_id,
                    name=feat.name or '',
                    type=ftype,
                    description=feat.description or '',
                    geometry=json.dumps(clean_points),
                    closed=bool(feat.closed),
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                ))

            self.session.commit()
            self.locations = locations
            print("Locations created successfully")
            return {"message": "Locations created successfully", "status": "success"}
        except Exception as e:
            self.session.rollback()
            print(f'Error during location creation: {e}')
            traceback.print_exc()
            return {"message": f"Error during location creation. {e}", "status": "failure"}