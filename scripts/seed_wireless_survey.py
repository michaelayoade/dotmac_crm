import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.db import SessionLocal
from app.models.person import Person
from app.models.wireless_survey import SurveyPointType
from app.schemas.wireless_survey import SurveyPointCreate, WirelessSiteSurveyCreate
from app.services import wireless_survey as ws_service


def parse_args():
    parser = argparse.ArgumentParser(description="Seed a demo wireless site survey.")
    parser.add_argument("--name", default="Demo Wireless Survey")
    parser.add_argument("--description", default="Sample survey created by seed script.")
    parser.add_argument("--lat", type=float, default=6.5244)
    parser.add_argument("--lon", type=float, default=3.3792)
    parser.add_argument("--offset", type=float, default=0.01)
    parser.add_argument("--frequency-mhz", type=float, default=5800.0)
    parser.add_argument("--antenna-height-m", type=float, default=12.0)
    parser.add_argument("--tx-power-dbm", type=float, default=20.0)
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()
    db = SessionLocal()
    try:
        person = db.query(Person).order_by(Person.created_at.asc()).first()
        person_id = person.id if person else None

        survey_payload = WirelessSiteSurveyCreate(
            name=args.name,
            description=args.description,
            frequency_mhz=args.frequency_mhz,
            default_antenna_height_m=args.antenna_height_m,
            default_tx_power_dbm=args.tx_power_dbm,
        )
        survey = ws_service.wireless_surveys.create(db, survey_payload, person_id)

        base_point = SurveyPointCreate(
            name="Base Station",
            point_type=SurveyPointType.tower,
            latitude=args.lat,
            longitude=args.lon,
            antenna_height_m=args.antenna_height_m,
        )
        customer_point = SurveyPointCreate(
            name="Customer Site",
            point_type=SurveyPointType.cpe,
            latitude=args.lat + args.offset,
            longitude=args.lon + args.offset,
            antenna_height_m=max(args.antenna_height_m - 3.0, 5.0),
        )
        base = ws_service.survey_points.create(db, survey.id, base_point)
        customer = ws_service.survey_points.create(db, survey.id, customer_point)
        ws_service.survey_los.analyze_path(db, survey.id, base.id, customer.id)

        print(f"Created survey: {survey.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
