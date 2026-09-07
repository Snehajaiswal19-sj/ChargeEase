import os
import re
import subprocess
import requests
import smtplib
import threading
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import secrets
import time
import json
import math
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from dotenv import load_dotenv
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from werkzeug.security import generate_password_hash, check_password_hash

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

app = Flask(__name__, template_folder=str(ROOT / "frontend" / "templates"), static_folder=str(ROOT / "frontend" / "static"))

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is missing. Create ChargeEase/.env from .env.example")
app.secret_key = SECRET_KEY

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI is missing. Create ChargeEase/.env from .env.example")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = client["ChargeEase"]
users_collection = db["users"]
stations_collection = db["stations"]
bookings_collection = db["bookings"]
reviews_collection = db["reviews"]
notifications_collection = db["notifications"]
favorites_collection = db["favorites"]

# Admin emails (comma-separated)
ADMIN_EMAILS = os.getenv("ADMIN_EMAILS", "admin@chargeease.com").split(",")

users_collection.create_index("email", unique=True)
stations_collection.create_index("station_id", unique=True)
stations_collection.create_index("ocm_id", unique=True, sparse=True)
reviews_collection.create_index([("user_id", 1), ("station_id", 1)], unique=True)
notifications_collection.create_index([("user_id", 1), ("created_at", -1)])
favorites_collection.create_index([("user_id", 1), ("station_id", 1)], unique=True)

C_DIR = ROOT / "C_DSA"
SEARCH_EXE = C_DIR / "search_station.exe"
SORT_EXE = C_DIR / "sorting.exe"
QUEUE_EXE = C_DIR / "queue.exe"

# Open Charge Map API key is optional for basic/low-volume use,
# but recommended - get a free one at https://openchargemap.org/site/develop/api
OCM_API_KEY = os.getenv("OCM_API_KEY", "")

# Email notifications (booking confirmed/cancelled) via Gmail SMTP.
# Use a Gmail "App Password", not your normal password: myaccount.google.com/apppasswords
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "")

def run_c(exe: Path, stdin: str = ""):
    if not exe.exists():
        raise FileNotFoundError(f"C executable missing: {exe}")
    return subprocess.run([str(exe)], input=stdin, text=True, capture_output=True, timeout=5)

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(a ** 0.5, (1 - a) ** 0.5)

def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = os.urandom(24).hex()
    return session["csrf_token"]

@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token}

@app.context_processor
def inject_admin_emails():
    return {"ADMIN_EMAILS": ADMIN_EMAILS}

def add_notification(user_id, message, ntype):
    notifications_collection.insert_one({
        "user_id": user_id,
        "message": message,
        "type": ntype,
        "read": False,
        "created_at": datetime.utcnow()
    })

def _send_email_now(to_email, subject, body):
    if not SMTP_EMAIL or not SMTP_APP_PASSWORD:
        print("Email not sent (SMTP_EMAIL / SMTP_APP_PASSWORD not configured):", subject)
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = f"ChargeEase <{SMTP_EMAIL}>"
        msg["To"] = to_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
            server.sendmail(SMTP_EMAIL, [to_email], msg.as_string())
    except Exception as e:
        print("Email send error:", e)

def send_email_async(to_email, subject, body):
    """Send email in a background thread so a slow/failing SMTP server never blocks the request."""
    threading.Thread(target=_send_email_now, args=(to_email, subject, body), daemon=True).start()

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session: return redirect(url_for("dashboard"))
    if request.method == "POST":
        name = request.form.get("name", "").strip(); email = request.form.get("email", "").strip().lower(); password = request.form.get("password", "")
        if not name or not email or not password:
            flash("Please fill all fields.", "error"); return redirect(url_for("register"))
        try:
            result = users_collection.insert_one({"name":name,"email":email,"password":generate_password_hash(password),"created_at":datetime.utcnow()})
        except DuplicateKeyError:
            flash("Email already registered. Please login.", "error"); return redirect(url_for("login"))
        session.update(user_id=str(result.inserted_id), user_name=name, user_email=email)
        flash("Registration successful!", "success"); return redirect(url_for("dashboard"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session: return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower(); password = request.form.get("password", "")
        user = users_collection.find_one({"email":email})
        if user and check_password_hash(user["password"], password):
            session.update(user_id=str(user["_id"]), user_name=user["name"], user_email=user["email"])
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); flash("You have been logged out.", "success"); return redirect(url_for("login"))

# =========================
# FORGOT / RESET PASSWORD
# =========================

RESET_TOKEN_VALID_MINUTES = 30

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if "user_id" in session: return redirect(url_for("dashboard"))

    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not submitted_token or submitted_token != session.get("csrf_token"):
            flash("Your session expired, please try again.", "error")
            return redirect(url_for("forgot_password"))

        email = request.form.get("email", "").strip().lower()
        user = users_collection.find_one({"email": email})

        if user:
            token = secrets.token_urlsafe(32)
            expires = datetime.utcnow() + timedelta(minutes=RESET_TOKEN_VALID_MINUTES)
            users_collection.update_one(
                {"_id": user["_id"]},
                {"$set": {"reset_token": token, "reset_token_expires": expires}}
            )
            reset_link = url_for("reset_password", token=token, _external=True)
            send_email_async(
                email,
                "ChargeEase - Reset Your Password",
                f"Hi {user['name']},\n\n"
                f"We received a request to reset your ChargeEase password. "
                f"This link is valid for {RESET_TOKEN_VALID_MINUTES} minutes:\n\n"
                f"{reset_link}\n\n"
                f"If you didn't request this, you can safely ignore this email.\n\n- ChargeEase"
            )

        # Same message whether or not the email is registered, so we don't leak account existence.
        flash("If that email is registered, a password reset link has been sent.", "success")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if "user_id" in session: return redirect(url_for("dashboard"))

    user = users_collection.find_one({"reset_token": token})
    if not user or user.get("reset_token_expires") is None or user["reset_token_expires"] < datetime.utcnow():
        flash("This reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not submitted_token or submitted_token != session.get("csrf_token"):
            flash("Your session expired, please try again.", "error")
            return redirect(url_for("reset_password", token=token))

        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
            return redirect(url_for("reset_password", token=token))

        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "error")
            return redirect(url_for("reset_password", token=token))

        users_collection.update_one(
            {"_id": user["_id"]},
            {
                "$set": {"password": generate_password_hash(new_password)},
                "$unset": {"reset_token": "", "reset_token_expires": ""}
            }
        )
        flash("Password reset successfully. Please login with your new password.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", token=token)

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session: return redirect(url_for("login"))
    from bson import ObjectId
    user = users_collection.find_one({"_id": ObjectId(session["user_id"])}, {"_id": 0, "vehicle": 1})
    vehicle = user.get("vehicle") if user else None
    
    # Get user's favorite stations
    user_favorites = list(favorites_collection.find({"user_id": session["user_id"]}))
    favorite_station_ids = [f["station_id"] for f in user_favorites]
    favorite_stations = list(stations_collection.find({"station_id": {"$in": favorite_station_ids}}, {"_id": 0}))
    
    return render_template("dashboard.html", vehicle=vehicle, favorite_stations=favorite_stations)

@app.route("/stations")
def stations():
    if "user_id" not in session:
        flash("Please login first to view charging stations.", "error")
        return redirect(url_for("login"))

    selected_charger_type = request.args.get("charger_type", "").strip()
    selected_connector_type = request.args.get("connector_type", "").strip()
    selected_max_distance = request.args.get("max_distance", "").strip()
    selected_min_rating = request.args.get("min_rating", "").strip()
    available_only = request.args.get("available_only") == "1"

    mongo_filter = {}
    if selected_charger_type:
        mongo_filter["charger_type"] = selected_charger_type
    if selected_connector_type:
        mongo_filter["connector_type"] = selected_connector_type
    if available_only:
        mongo_filter["available_slots"] = {"$gt": 0}

    stations = list(stations_collection.find(mongo_filter, {"_id": 0}))

    if selected_max_distance:
        try:
            max_dist = float(selected_max_distance)
            stations = [s for s in stations if s.get("distance", 999999) <= max_dist]
        except ValueError:
            pass

    charger_types = sorted(stations_collection.distinct("charger_type"))
    connector_types = sorted(stations_collection.distinct("connector_type"))

    # Get user's favorite station IDs
    user_favorites = list(favorites_collection.find({"user_id": session["user_id"]}))
    favorite_station_ids = {f["station_id"] for f in user_favorites}

    # Sorting via C
    try:
        c_input = str(len(stations)) + "\n"
        for s in stations:
            c_input += f"{s['station_id']} {s.get('distance', 0)}\n"

        process = subprocess.run([os.path.join(app.root_path, "..", "C_DSA", "sorting.exe")], input=c_input, text=True, capture_output=True, timeout=5)
        sorted_ids = []
        for line in process.stdout.strip().splitlines():
            if "|" in line:
                station_id, _ = line.split("|")
                sorted_ids.append(int(station_id))

        station_map = {station["station_id"]: station for station in stations}
        stations = [station_map[sid] for sid in sorted_ids if sid in station_map]
    except Exception as e:
        print("C sorting error:", e)
        stations.sort(key=lambda x: x.get("distance", 999999))

    for station in stations:
        s_reviews = list(reviews_collection.find({"station_id": station["station_id"]}, {"rating": 1}))
        station["review_count"] = len(s_reviews)
        station["avg_rating"] = round(sum(r["rating"] for r in s_reviews) / len(s_reviews), 1) if s_reviews else None
        station["is_favorite"] = station["station_id"] in favorite_station_ids

    if selected_min_rating:
        try:
            min_rating = float(selected_min_rating)
            stations = [s for s in stations if s["avg_rating"] is not None and s["avg_rating"] >= min_rating]
        except ValueError:
            pass

    return render_template("stations.html", stations=stations, charger_types=charger_types, connector_types=connector_types, selected_charger_type=selected_charger_type, selected_connector_type=selected_connector_type, selected_max_distance=selected_max_distance, selected_min_rating=selected_min_rating, available_only=available_only, search_result=request.args.get("search_result", ""), searched_station_id=request.args.get("searched_id", ""))

@app.route("/api/search-station")
def live_search_station():
    if "user_id" not in session:
        return {"error": "LOGIN_REQUIRED"}, 401

    query = request.args.get("q", "").strip()

    if not query:
        return {"result": "EMPTY"}

    if query.isdigit():
        station_id = int(query)
        try:
            all_stations = list(stations_collection.find({}, {"_id": 0, "station_id": 1}))
            c_input = str(len(all_stations)) + "\n"
            for s in all_stations:
                c_input += str(s["station_id"]) + "\n"
            c_input += query + "\n"

            process = run_c(SEARCH_EXE, c_input)
            output = process.stdout.strip()

            if output.startswith("FOUND"):
                station = stations_collection.find_one({"station_id": station_id}, {"_id": 0})
                if station:
                    return {"result": "FOUND", "station": station}
            return {"result": "NOT_FOUND"}
        except Exception as e:
            print("C search error:", e)
            return {"result": "ERROR"}

    safe_query = re.escape(query)
    stations = list(stations_collection.find({"$or": [{"name": {"$regex": safe_query, "$options": "i"}}, {"location": {"$regex": safe_query, "$options": "i"}}]}, {"_id": 0}).limit(10))

    if stations:
        return {"result": "MULTIPLE", "stations": stations}

    coords = geocode_location(query)
    if coords:
        lat, lng = coords
        external_chargers = fetch_ocm_chargers(lat, lng, distance_km=20, maxresults=15)
        if external_chargers:
            return {"result": "EXTERNAL", "stations": [], "external_chargers": external_chargers}

    return {"result": "NOT_FOUND"}

@app.route("/queue-station/<int:station_id>")
def queue_station(station_id):
    if "user_id" not in session:
        flash("Please login first.", "error")
        return redirect(url_for("login"))

    station = stations_collection.find_one({"station_id": station_id}, {"_id": 0})
    if station is None:
        flash("Charging station not found.", "error")
        return redirect(url_for("stations"))

    waiting = int(station.get("waiting_vehicles", 0))
    queue_status = "ERROR"

    try:
        process = subprocess.run([os.path.join(app.root_path, "..", "C_DSA", "queue.exe")], input=f"{station_id} {waiting}\n", text=True, capture_output=True, timeout=5)
        if process.stdout.strip().startswith("QUEUE_OK"):
            queue_status = "QUEUE_OK"
    except Exception as e:
        print("C queue error:", e)

    return render_template("station_details.html", station=station, queue_status=queue_status)

@app.route("/station/<int:station_id>")
def station_details(station_id):
    if "user_id" not in session: return redirect(url_for("login"))
    station = stations_collection.find_one({"station_id":station_id},{"_id":0})
    if not station: flash("Charging station not found.","error"); return redirect(url_for("stations"))

    reviews = list(reviews_collection.find({"station_id": station_id}, {"_id": 0}).sort("created_at", -1))
    review_count = len(reviews)
    avg_rating = round(sum(r["rating"] for r in reviews) / review_count, 1) if review_count else None

    has_booking = bookings_collection.count_documents({"user_id": session["user_id"], "station_id": station_id}) > 0
    my_review = reviews_collection.find_one({"user_id": session["user_id"], "station_id": station_id}, {"_id": 0})
    is_favorite = favorites_collection.count_documents({"user_id": session["user_id"], "station_id": station_id}) > 0

    return render_template("station_details.html", station=station, reviews=reviews, review_count=review_count, avg_rating=avg_rating, can_review=has_booking, my_review=my_review, is_favorite=is_favorite)

@app.route("/station/<int:station_id>/review", methods=["POST"])
def submit_review(station_id):
    if "user_id" not in session: return redirect(url_for("login"))
    submitted_token = request.form.get("csrf_token", "")
    if not submitted_token or submitted_token != session.get("csrf_token"):
        flash("Your session expired, please try again.", "error")
        return redirect(url_for("station_details", station_id=station_id))

    station = stations_collection.find_one({"station_id": station_id})
    if not station:
        flash("Charging station not found.", "error")
        return redirect(url_for("stations"))

    has_booking = bookings_collection.count_documents({"user_id": session["user_id"], "station_id": station_id}) > 0
    if not has_booking:
        flash("You can rate a station only after booking a slot there.", "error")
        return redirect(url_for("station_details", station_id=station_id))

    try:
        rating = int(request.form.get("rating", ""))
        if rating < 1 or rating > 5:
            raise ValueError()
    except ValueError:
        flash("Please select a rating between 1 and 5.", "error")
        return redirect(url_for("station_details", station_id=station_id))

    review_text = request.form.get("review_text", "").strip()[:500]

    reviews_collection.update_one({"user_id": session["user_id"], "station_id": station_id}, {"$set": {"user_id": session["user_id"], "user_name": session["user_name"], "station_id": station_id, "rating": rating, "review_text": review_text, "created_at": datetime.utcnow()}}, upsert=True)
    flash("Thanks for your review!", "success")
    return redirect(url_for("station_details", station_id=station_id))

@app.route("/book/<int:station_id>", methods=["POST"])
def book_station(station_id):
    if "user_id" not in session: return redirect(url_for("login"))
    submitted_token = request.form.get("csrf_token", "")
    if not submitted_token or submitted_token != session.get("csrf_token"):
        flash("Your session expired, please try booking again.","error"); return redirect(url_for("station_details",station_id=station_id))
    station = stations_collection.find_one({"station_id":station_id})
    if not station: flash("Charging station not found.","error"); return redirect(url_for("stations"))
    updated = stations_collection.find_one_and_update({"station_id":station_id,"available_slots":{"$gt":0}}, {"$inc":{"available_slots":-1}}, return_document=ReturnDocument.AFTER)
    if not updated: flash("Sorry, no charging slots are currently available.","error"); return redirect(url_for("station_details",station_id=station_id))
    bookings_collection.insert_one({"user_id":session["user_id"],"user_name":session["user_name"],"user_email":session["user_email"],"station_id":station_id,"station_name":station["name"],"location":station["location"],"status":"reserved","created_at":datetime.utcnow()})
    add_notification(session["user_id"], f"Booking confirmed for {station['name']}.", "booking_confirmed")
    send_email_async(session["user_email"], "ChargeEase - Booking Confirmed", f"Hi {session['user_name']},\n\nYour charging slot at {station['name']} ({station['location']}) is confirmed.\n\nSee you there!\n- ChargeEase")
    flash("Charging slot reserved successfully!","success"); return redirect(url_for("station_details",station_id=station_id))

@app.route("/bookings")
def bookings():
    if "user_id" not in session: return redirect(url_for("login"))
    user_bookings = list(bookings_collection.find({"user_id":session["user_id"]}).sort("created_at",-1))
    return render_template("bookings.html", bookings=user_bookings)

@app.route("/bookings/<booking_id>/cancel", methods=["POST"])
def cancel_booking(booking_id):
    if "user_id" not in session: return redirect(url_for("login"))
    submitted_token = request.form.get("csrf_token", "")
    if not submitted_token or submitted_token != session.get("csrf_token"):
        flash("Your session expired, please try again.", "error")
        return redirect(url_for("bookings"))

    from bson import ObjectId
    try:
        booking_oid = ObjectId(booking_id)
    except Exception:
        flash("Booking not found.", "error")
        return redirect(url_for("bookings"))

    booking = bookings_collection.find_one({"_id": booking_oid, "user_id": session["user_id"]})
    if not booking:
        flash("Booking not found.", "error")
        return redirect(url_for("bookings"))

    if booking["status"] != "reserved":
        flash("This booking is already cancelled.", "error")
        return redirect(url_for("bookings"))

    bookings_collection.update_one({"_id": booking_oid}, {"$set": {"status": "cancelled"}})
    stations_collection.update_one({"station_id": booking["station_id"]}, {"$inc": {"available_slots": 1}})
    add_notification(session["user_id"], f"Booking cancelled for {booking['station_name']}.", "booking_cancelled")
    send_email_async(session["user_email"], "ChargeEase - Booking Cancelled", f"Hi {session['user_name']},\n\nYour booking at {booking['station_name']} has been cancelled and the slot released.\n\n- ChargeEase")

    flash("Booking cancelled and slot released.", "success")
    return redirect(url_for("bookings"))

# =========================
# NOTIFICATIONS
# =========================

@app.route("/api/notifications")
def get_notifications():
    if "user_id" not in session:
        return {"error": "LOGIN_REQUIRED"}, 401

    items = list(notifications_collection.find({"user_id": session["user_id"]}).sort("created_at", -1).limit(20))
    for item in items:
        item["_id"] = str(item["_id"])
        item["created_at"] = item["created_at"].strftime("%d %b, %I:%M %p")

    unread_count = notifications_collection.count_documents({"user_id": session["user_id"], "read": False})
    return {"result": "OK", "notifications": items, "unread_count": unread_count}

@app.route("/api/notifications/mark-read", methods=["POST"])
def mark_notifications_read():
    if "user_id" not in session:
        return {"error": "LOGIN_REQUIRED"}, 401
    submitted_token = request.headers.get("X-CSRFToken", "")
    if not submitted_token or submitted_token != session.get("csrf_token"):
        return {"error": "INVALID_CSRF"}, 403
    notifications_collection.update_many({"user_id": session["user_id"], "read": False}, {"$set": {"read": True}})
    return {"result": "OK"}

@app.route("/about")
def about(): return render_template("about.html")

# =========================
# VEHICLE PROFILE
# =========================

VALID_CONNECTORS = {"CCS2", "Type 2", "CHAdeMO", "GB/T"}

@app.route("/vehicle-profile", methods=["GET", "POST"])
def vehicle_profile():
    if "user_id" not in session: return redirect(url_for("login"))
    from bson import ObjectId
    user_oid = ObjectId(session["user_id"])

    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not submitted_token or submitted_token != session.get("csrf_token"):
            flash("Your session expired, please try again.", "error")
            return redirect(url_for("vehicle_profile"))

        model = request.form.get("vehicle_model", "").strip()
        connector = request.form.get("connector_type", "").strip()
        battery_raw = request.form.get("battery_capacity", "").strip()

        if not model or connector not in VALID_CONNECTORS:
            flash("Please provide a valid vehicle model and connector type.", "error")
            return redirect(url_for("vehicle_profile"))

        try:
            battery_capacity = float(battery_raw) if battery_raw else None
            if battery_capacity is not None and (battery_capacity <= 0 or battery_capacity > 300):
                raise ValueError()
        except ValueError:
            flash("Please enter a valid battery capacity in kWh.", "error")
            return redirect(url_for("vehicle_profile"))

        users_collection.update_one({"_id": user_oid}, {"$set": {"vehicle": {"model": model, "connector_type": connector, "battery_capacity": battery_capacity}}})
        flash("Vehicle profile saved.", "success")
        return redirect(url_for("dashboard"))

    user = users_collection.find_one({"_id": user_oid}, {"_id": 0, "vehicle": 1})
    vehicle = user.get("vehicle") if user else None
    return render_template("vehicle_profile.html", vehicle=vehicle, connectors=sorted(VALID_CONNECTORS))

# =========================
# ACCOUNT SECURITY - PASSWORD CHANGE
# =========================

@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if "user_id" not in session: return redirect(url_for("login"))
    from bson import ObjectId
    user_oid = ObjectId(session["user_id"])

    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not submitted_token or submitted_token != session.get("csrf_token"):
            flash("Your session expired, please try again.", "error")
            return redirect(url_for("change_password"))

        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        user = users_collection.find_one({"_id": user_oid})
        if not user or not check_password_hash(user["password"], current_password):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("change_password"))

        if len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
            return redirect(url_for("change_password"))

        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "error")
            return redirect(url_for("change_password"))

        users_collection.update_one({"_id": user_oid}, {"$set": {"password": generate_password_hash(new_password)}})
        flash("Password updated successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("change_password.html")

# =========================
# LOCATION-BASED NEARBY STATIONS
# =========================

@app.route("/api/nearby-stations")
def nearby_stations():
    if "user_id" not in session:
        return {"error": "LOGIN_REQUIRED"}, 401

    try:
        user_lat = float(request.args.get("lat", ""))
        user_lng = float(request.args.get("lng", ""))
    except ValueError:
        return {"result": "INVALID_LOCATION"}, 400

    if not (-90 <= user_lat <= 90 and -180 <= user_lng <= 180):
        return {"result": "INVALID_LOCATION"}, 400

    all_stations = list(stations_collection.find({}, {"_id": 0}))

    stations_with_distance = []
    for s in all_stations:
        if "latitude" not in s or "longitude" not in s:
            continue
        real_distance = round(haversine_km(user_lat, user_lng, s["latitude"], s["longitude"]), 2)
        s_copy = dict(s)
        s_copy["distance"] = real_distance
        stations_with_distance.append(s_copy)

    try:
        c_input = str(len(stations_with_distance)) + "\n"
        for s in stations_with_distance:
            c_input += f"{s['station_id']} {s['distance']}\n"

        process = run_c(SORT_EXE, c_input)
        sorted_ids = []
        for line in process.stdout.strip().splitlines():
            if "|" in line:
                sid, _ = line.split("|")
                sorted_ids.append(int(sid))

        station_map = {s["station_id"]: s for s in stations_with_distance}
        sorted_stations = [station_map[sid] for sid in sorted_ids if sid in station_map]
        if len(sorted_stations) == len(stations_with_distance):
            stations_with_distance = sorted_stations
    except Exception as e:
        print("C sorting error (nearby):", e)
        stations_with_distance.sort(key=lambda x: x["distance"])

    return {"result": "OK", "stations": stations_with_distance[:10]}

# =========================
# REAL-WORLD NEARBY CHARGERS (Open Charge Map)
# =========================

CONNECTOR_MAP = {
    "ccs": "CCS2", "type 2": "Type 2", "type2": "Type 2",
    "chademo": "CHAdeMO", "gb/t": "GB/T", "gbt": "GB/T",
}

def derive_connector_and_type(connections):
    connector_type = "Type 2"
    max_kw = 0
    for c in connections or []:
        title = ((c.get("ConnectionType") or {}).get("Title") or "").lower()
        for key, mapped in CONNECTOR_MAP.items():
            if key in title:
                connector_type = mapped
                break
        power = c.get("PowerKW") or 0
        if power > max_kw:
            max_kw = power
    if max_kw >= 43:
        charger_type = "DC Fast Charger"
    elif max_kw >= 22:
        charger_type = "Fast Charger"
    else:
        charger_type = "AC Charger"
    return connector_type, charger_type

def fetch_ocm_chargers(lat, lng, distance_km=15, maxresults=25):
    """Fetch real-world EV chargers near a point from Open Charge Map."""
    params = {
        "output": "json",
        "latitude": lat,
        "longitude": lng,
        "distance": distance_km,
        "maxresults": maxresults,
        "compact": "true",
        "verbose": "false",
    }
    if OCM_API_KEY:
        params["key"] = OCM_API_KEY

    try:
        resp = requests.get("https://api.openchargemap.io/v3/poi/", params=params, headers={"User-Agent": "ChargeEase-EV-App/1.0 (student project)"}, timeout=8)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        print("Open Charge Map error:", e)
        return []

    chargers = []
    for poi in raw:
        addr = poi.get("AddressInfo") or {}
        c_lat = addr.get("Latitude")
        c_lng = addr.get("Longitude")
        if c_lat is None or c_lng is None:
            continue
        chargers.append({
            "id": poi.get("ID"),
            "name": addr.get("Title", "Unnamed charger"),
            "address": addr.get("AddressLine1", ""),
            "town": addr.get("Town", ""),
            "latitude": c_lat,
            "longitude": c_lng,
            "distance_km": round(addr.get("Distance", 0), 2) if addr.get("Distance") is not None else None,
            "operator": (poi.get("OperatorInfo") or {}).get("Title", "Unknown operator"),
        })
    return chargers

def geocode_location(query):
    """Convert a free-text place name into (lat, lng) using OpenStreetMap's Nominatim."""
    try:
        resp = requests.get("https://nominatim.openstreetmap.org/search", params={"q": query, "format": "json", "limit": 1}, headers={"User-Agent": "ChargeEase-EV-App/1.0"}, timeout=6)
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        print("Geocoding error:", e)
    return None

@app.route("/api/nearby-chargers")
def nearby_chargers():
    if "user_id" not in session:
        return {"error": "LOGIN_REQUIRED"}, 401

    try:
        user_lat = float(request.args.get("lat", ""))
        user_lng = float(request.args.get("lng", ""))
    except ValueError:
        return {"result": "INVALID_LOCATION"}, 400

    if not (-90 <= user_lat <= 90 and -180 <= user_lng <= 180):
        return {"result": "INVALID_LOCATION"}, 400

    chargers = fetch_ocm_chargers(user_lat, user_lng, distance_km=15, maxresults=25)
    return {"result": "OK", "chargers": chargers}

# =========================
# INTERACTIVE MAP
# =========================

@app.route("/map")
def station_map():
    if "user_id" not in session: return redirect(url_for("login"))
    from bson import ObjectId
    user = users_collection.find_one({"_id": ObjectId(session["user_id"])}, {"_id": 0, "vehicle": 1})
    user_connector = (user.get("vehicle") or {}).get("connector_type") if user else None

    stations = list(stations_collection.find({"latitude": {"$exists": True}, "longitude": {"$exists": True}}, {"_id": 0}))
    return render_template("map.html", stations=stations, user_connector=user_connector)

# =========================
# EXTERNAL (REAL-WORLD) STATION DETAILS
# =========================

@app.route("/external-station/<int:ocm_id>")
def external_station(ocm_id):
    if "user_id" not in session: return redirect(url_for("login"))

    params = {"output": "json", "chargepointid": ocm_id, "verbose": "false"}
    if OCM_API_KEY:
        params["key"] = OCM_API_KEY

    try:
        resp = requests.get("https://api.openchargemap.io/v3/poi/", params=params, headers={"User-Agent": "ChargeEase-EV-App/1.0 (student project)"}, timeout=8)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        print("Open Charge Map error:", e)
        flash("Could not load this charger's details right now.", "error")
        return redirect(url_for("stations"))

    if not raw:
        flash("Charger not found.", "error")
        return redirect(url_for("stations"))

    poi = raw[0]
    addr = poi.get("AddressInfo") or {}
    connections = poi.get("Connections") or []

    charger = {
        "id": poi.get("ID"),
        "name": addr.get("Title", "Unnamed charger"),
        "address": addr.get("AddressLine1", ""),
        "town": addr.get("Town", ""),
        "postcode": addr.get("Postcode", ""),
        "latitude": addr.get("Latitude"),
        "longitude": addr.get("Longitude"),
        "operator": (poi.get("OperatorInfo") or {}).get("Title", "Unknown operator"),
        "num_points": poi.get("NumberOfPoints"),
        "connections": [{"type": (c.get("ConnectionType") or {}).get("Title", "Unknown"), "power_kw": c.get("PowerKW"), "quantity": c.get("Quantity") or 1} for c in connections],
        "usage_cost": poi.get("UsageCost"),
        "last_verified": (poi.get("StatusType") or {}).get("Title"),
    }

    return render_template("external_station.html", charger=charger)

@app.route("/import-and-book/<int:ocm_id>")
def import_and_book(ocm_id):
    if "user_id" not in session: return redirect(url_for("login"))

    existing = stations_collection.find_one({"ocm_id": ocm_id})
    if existing:
        return redirect(url_for("station_details", station_id=existing["station_id"]))

    params = {"output": "json", "chargepointid": ocm_id, "verbose": "false"}
    if OCM_API_KEY:
        params["key"] = OCM_API_KEY

    try:
        resp = requests.get("https://api.openchargemap.io/v3/poi/", params=params, headers={"User-Agent": "ChargeEase-EV-App/1.0 (student project)"}, timeout=8)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        print("Open Charge Map error:", e)
        flash("Could not import this station right now.", "error")
        return redirect(url_for("stations"))

    if not raw:
        flash("Charger not found.", "error")
        return redirect(url_for("stations"))

    poi = raw[0]
    addr = poi.get("AddressInfo") or {}
    lat, lng = addr.get("Latitude"), addr.get("Longitude")
    if lat is None or lng is None:
        flash("This charger doesn't have location data, can't add it for booking.", "error")
        return redirect(url_for("stations"))

    connector_type, charger_type = derive_connector_and_type(poi.get("Connections"))
    total_slots = max(1, int(poi.get("NumberOfPoints") or 2))

    last_station = stations_collection.find_one(sort=[("station_id", -1)])
    next_id = (last_station["station_id"] + 1) if last_station else 101

    town = addr.get("Town", "")
    location = f"{addr.get('AddressLine1', '')}, {town}".strip(", ") or "Unknown location"

    doc = {
        "station_id": next_id,
        "ocm_id": ocm_id,
        "name": addr.get("Title", "Charging Station"),
        "location": location,
        "charger_type": charger_type,
        "connector_type": connector_type,
        "total_slots": total_slots,
        "available_slots": total_slots,
        "waiting_vehicles": 0,
        "distance": 0,
        "latitude": lat,
        "longitude": lng,
    }
    for attempt in range(5):
        try:
            stations_collection.insert_one(doc)
            break
        except DuplicateKeyError:
            doc["station_id"] += 1
    else:
        flash("Could not import this station right now, please try again.", "error")
        return redirect(url_for("stations"))

    return redirect(url_for("station_details", station_id=doc["station_id"]))

# =========================
# SMART RECOMMENDATION ENGINE
# =========================

def calculate_recommendation_score(station, user_lat, user_lng, user_connector):
    W_DISTANCE = 40
    W_AVAILABILITY = 30
    W_RATING = 20
    W_CONNECTOR = 10
    
    score = 0
    
    try:
        distance = station.get("distance", 999)
        if distance <= 5:
            score += W_DISTANCE
        elif distance <= 10:
            score += W_DISTANCE * 0.7
        elif distance <= 20:
            score += W_DISTANCE * 0.4
        elif distance <= 50:
            score += W_DISTANCE * 0.15
    except:
        pass
    
    try:
        total = station.get("total_slots", 0)
        available = station.get("available_slots", 0)
        if total > 0:
            score += W_AVAILABILITY * (available / total)
    except:
        pass
    
    try:
        rating = station.get("avg_rating", None)
        if rating is None:
            score += W_RATING * 0.4
        elif rating >= 4.5:
            score += W_RATING
        elif rating >= 4.0:
            score += W_RATING * 0.8
        elif rating >= 3.5:
            score += W_RATING * 0.7
        elif rating >= 3.0:
            score += W_RATING * 0.6
        elif rating >= 2.0:
            score += W_RATING * 0.4
        else:
            score += W_RATING * 0.2
    except:
        pass
    
    try:
        station_connector = station.get("connector_type", "")
        if user_connector and station_connector:
            if station_connector == user_connector:
                score += W_CONNECTOR
        else:
            score += W_CONNECTOR * 0.3
    except:
        pass
    
    return round(min(score, 100), 1)

@app.route("/recommendations")
def recommendations():
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    from bson import ObjectId
    user = users_collection.find_one({"_id": ObjectId(session["user_id"])})
    user_vehicle = (user.get("vehicle") or {}) if user else {}
    user_connector = user_vehicle.get("connector_type")
    
    stations_list = list(stations_collection.find({}, {"_id": 0}))
    
    for station in stations_list:
        s_reviews = list(reviews_collection.find({"station_id": station["station_id"]}, {"rating": 1}))
        station["review_count"] = len(s_reviews)
        station["avg_rating"] = round(sum(r["rating"] for r in s_reviews) / len(s_reviews), 1) if s_reviews else None
    
    for station in stations_list:
        station["recommendation_score"] = calculate_recommendation_score(station, None, None, user_connector)
    
    stations_list.sort(key=lambda x: x["recommendation_score"], reverse=True)
    top_recommendations = stations_list[:10]
    
    return render_template("recommendations.html", recommendations=top_recommendations, user_vehicle=user_vehicle, user_connector=user_connector)

# =========================
# FAVORITE STATIONS
# =========================

@app.route("/favorites")
def favorites():
    """View favorite stations"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    user_favorites = list(favorites_collection.find({"user_id": session["user_id"]}))
    favorite_station_ids = [f["station_id"] for f in user_favorites]
    
    favorite_stations = []
    for sid in favorite_station_ids:
        station = stations_collection.find_one({"station_id": sid}, {"_id": 0})
        if station:
            # Add rating info
            s_reviews = list(reviews_collection.find({"station_id": sid}, {"rating": 1}))
            station["review_count"] = len(s_reviews)
            station["avg_rating"] = round(sum(r["rating"] for r in s_reviews) / len(s_reviews), 1) if s_reviews else None
            favorite_stations.append(station)
    
    return render_template("favorites.html", stations=favorite_stations)

@app.route("/favorite/<int:station_id>", methods=["POST"])
def add_favorite(station_id):
    """Add station to favorites"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    submitted_token = request.form.get("csrf_token", "")
    if not submitted_token or submitted_token != session.get("csrf_token"):
        flash("Your session expired, please try again.", "error")
        return redirect(url_for("station_details", station_id=station_id))
    
    station = stations_collection.find_one({"station_id": station_id})
    if not station:
        flash("Charging station not found.", "error")
        return redirect(url_for("stations"))
    
    try:
        favorites_collection.insert_one({
            "user_id": session["user_id"],
            "station_id": station_id,
            "station_name": station["name"],
            "created_at": datetime.utcnow()
        })
        flash(f"Added '{station['name']}' to favorites!", "success")
    except DuplicateKeyError:
        flash("This station is already in your favorites.", "error")
    
    return redirect(url_for("station_details", station_id=station_id))

@app.route("/favorite/<int:station_id>/remove", methods=["POST"])
def remove_favorite(station_id):
    """Remove station from favorites"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    submitted_token = request.form.get("csrf_token", "")
    if not submitted_token or submitted_token != session.get("csrf_token"):
        flash("Your session expired, please try again.", "error")
        return redirect(url_for("favorites"))
    
    favorites_collection.delete_one({"user_id": session["user_id"], "station_id": station_id})
    flash("Station removed from favorites.", "success")
    
    # Redirect back to appropriate page
    referrer = request.referrer
    if referrer and "favorites" in referrer:
        return redirect(url_for("favorites"))
    return redirect(url_for("station_details", station_id=station_id))

# =========================
# CHARGING COST ESTIMATOR
# =========================

@app.route("/cost-estimator", methods=["GET", "POST"])
def cost_estimator():
    """Estimate charging time and cost"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    result = None
    
    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not submitted_token or submitted_token != session.get("csrf_token"):
            flash("Your session expired, please try again.", "error")
            return redirect(url_for("cost_estimator"))
        
        try:
            battery_capacity = float(request.form.get("battery_capacity", ""))
            current_percent = float(request.form.get("current_percent", ""))
            target_percent = float(request.form.get("target_percent", ""))
            charger_power = float(request.form.get("charger_power", ""))
            cost_per_kwh = float(request.form.get("cost_per_kwh", ""))
            
            if battery_capacity <= 0 or current_percent < 0 or current_percent > 100 or target_percent <= current_percent or target_percent > 100:
                flash("Please enter valid values.", "error")
                return redirect(url_for("cost_estimator"))
            
            if charger_power <= 0 or cost_per_kwh < 0:
                flash("Please enter valid charger power and cost.", "error")
                return redirect(url_for("cost_estimator"))
            
            # Calculate energy needed (kWh)
            energy_needed = battery_capacity * (target_percent - current_percent) / 100
            
            # Calculate time (hours) = energy / power
            # Charging efficiency ~90%
            efficiency = 0.9
            time_hours = energy_needed / (charger_power * efficiency)
            time_minutes = round(time_hours * 60, 1)
            
            # Calculate cost
            cost = round(energy_needed * cost_per_kwh, 2)
            
            result = {
                "energy_needed": round(energy_needed, 2),
                "time_minutes": time_minutes,
                "time_hours": round(time_hours, 2),
                "cost": cost,
                "battery_capacity": battery_capacity,
                "current_percent": current_percent,
                "target_percent": target_percent,
                "charger_power": charger_power,
                "cost_per_kwh": cost_per_kwh
            }
            
        except ValueError:
            flash("Please enter valid numbers.", "error")
            return redirect(url_for("cost_estimator"))
    
    return render_template("cost_estimator.html", result=result)

# =========================
# EV RANGE CALCULATOR
# =========================

@app.route("/range-calculator", methods=["GET", "POST"])
def range_calculator():
    """Calculate how far you can go with current battery"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    result = None
    
    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not submitted_token or submitted_token != session.get("csrf_token"):
            flash("Your session expired, please try again.", "error")
            return redirect(url_for("range_calculator"))
        
        try:
            battery_capacity = float(request.form.get("battery_capacity", ""))
            current_percent = float(request.form.get("current_percent", ""))
            efficiency = float(request.form.get("efficiency", ""))
            
            if battery_capacity <= 0 or current_percent < 0 or current_percent > 100 or efficiency <= 0:
                flash("Please enter valid values.", "error")
                return redirect(url_for("range_calculator"))
            
            # Available energy (kWh)
            available_energy = battery_capacity * current_percent / 100
            
            # Range (km) = available_energy * efficiency (km/kWh)
            range_km = round(available_energy * efficiency, 1)
            
            # Also show range at different speeds
            # Typical efficiency varies: city ~6 km/kWh, highway ~4 km/kWh, mixed ~5 km/kWh
            city_range = round(available_energy * (efficiency * 1.2), 1)  # 20% better in city
            highway_range = round(available_energy * (efficiency * 0.8), 1)  # 20% worse on highway
            
            result = {
                "available_energy": round(available_energy, 2),
                "range_km": range_km,
                "city_range": city_range,
                "highway_range": highway_range,
                "battery_capacity": battery_capacity,
                "current_percent": current_percent,
                "efficiency": efficiency
            }
            
        except ValueError:
            flash("Please enter valid numbers.", "error")
            return redirect(url_for("range_calculator"))
    
    return render_template("range_calculator.html", result=result)

# =========================
# ADMIN DASHBOARD
# =========================

def is_admin():
    """Check if current user is admin"""
    if "user_id" not in session:
        return False
    if not ADMIN_EMAILS or ADMIN_EMAILS == [""] or ADMIN_EMAILS[0].strip().upper() == "ALL":
        return True
    return session.get("user_email") in [email.strip().lower() for email in ADMIN_EMAILS]

def admin_required(f):
    """Decorator to require admin access"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin():
            flash("Admin access required.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/admin")
@admin_required
def admin_dashboard():
    """Admin dashboard - overview"""
    total_users = users_collection.count_documents({})
    total_stations = stations_collection.count_documents({})
    total_bookings = bookings_collection.count_documents({})
    active_bookings = bookings_collection.count_documents({"status": "reserved"})
    total_reviews = reviews_collection.count_documents({})
    
    recent_bookings = list(bookings_collection.find().sort("created_at", -1).limit(10))
    recent_users = list(users_collection.find({}, {"_id": 1, "name": 1, "email": 1, "created_at": 1}).sort("created_at", -1).limit(5))
    
    return render_template("admin_dashboard.html",
        total_users=total_users,
        total_stations=total_stations,
        total_bookings=total_bookings,
        active_bookings=active_bookings,
        total_reviews=total_reviews,
        recent_bookings=recent_bookings,
        recent_users=recent_users
    )

@app.route("/admin/stations")
@admin_required
def admin_stations():
    """Admin - manage stations"""
    stations_list = list(stations_collection.find({}, {"_id": 0}).sort("station_id", 1))
    return render_template("admin_stations.html", stations=stations_list)

@app.route("/admin/stations/add", methods=["GET", "POST"])
@admin_required
def admin_add_station():
    """Admin - add new station"""
    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not submitted_token or submitted_token != session.get("csrf_token"):
            flash("Your session expired, please try again.", "error")
            return redirect(url_for("admin_add_station"))
        
        name = request.form.get("name", "").strip()
        location = request.form.get("location", "").strip()
        charger_type = request.form.get("charger_type", "").strip()
        connector_type = request.form.get("connector_type", "").strip()
        total_slots = request.form.get("total_slots", "").strip()
        latitude = request.form.get("latitude", "").strip()
        longitude = request.form.get("longitude", "").strip()
        
        if not name or not location or not charger_type or not connector_type or not total_slots:
            flash("Please fill all required fields.", "error")
            return redirect(url_for("admin_add_station"))
        
        try:
            total_slots = int(total_slots)
            if total_slots <= 0:
                raise ValueError()
        except ValueError:
            flash("Please enter a valid number of slots.", "error")
            return redirect(url_for("admin_add_station"))
        
        last_station = stations_collection.find_one(sort=[("station_id", -1)])
        next_id = (last_station["station_id"] + 1) if last_station else 101
        
        try:
            distance = float(request.form.get("distance", "0"))
        except ValueError:
            distance = 0
        
        station_doc = {
            "station_id": next_id,
            "name": name,
            "location": location,
            "charger_type": charger_type,
            "connector_type": connector_type,
            "total_slots": total_slots,
            "available_slots": total_slots,
            "waiting_vehicles": 0,
            "distance": distance,
            "latitude": float(latitude) if latitude else None,
            "longitude": float(longitude) if longitude else None,
            "created_at": datetime.utcnow()
        }
        
        stations_collection.insert_one(station_doc)
        flash(f"Station '{name}' added successfully!", "success")
        return redirect(url_for("admin_stations"))
    
    return render_template("admin_add_station.html")

@app.route("/admin/stations/edit/<int:station_id>", methods=["GET", "POST"])
@admin_required
def admin_edit_station(station_id):
    """Admin - edit station"""
    station = stations_collection.find_one({"station_id": station_id}, {"_id": 0})
    if not station:
        flash("Station not found.", "error")
        return redirect(url_for("admin_stations"))
    
    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not submitted_token or submitted_token != session.get("csrf_token"):
            flash("Your session expired, please try again.", "error")
            return redirect(url_for("admin_edit_station", station_id=station_id))
        
        name = request.form.get("name", "").strip()
        location = request.form.get("location", "").strip()
        charger_type = request.form.get("charger_type", "").strip()
        connector_type = request.form.get("connector_type", "").strip()
        total_slots = request.form.get("total_slots", "").strip()
        available_slots = request.form.get("available_slots", "").strip()
        
        if not name or not location or not charger_type or not connector_type or not total_slots:
            flash("Please fill all required fields.", "error")
            return redirect(url_for("admin_edit_station", station_id=station_id))
        
        try:
            total_slots = int(total_slots)
            available_slots = int(available_slots)
            if total_slots <= 0 or available_slots < 0 or available_slots > total_slots:
                raise ValueError()
        except ValueError:
            flash("Please enter valid slot numbers.", "error")
            return redirect(url_for("admin_edit_station", station_id=station_id))
        
        update_data = {
            "name": name,
            "location": location,
            "charger_type": charger_type,
            "connector_type": connector_type,
            "total_slots": total_slots,
            "available_slots": available_slots,
        }
        
        # Optional fields
        if request.form.get("latitude", "").strip():
            update_data["latitude"] = float(request.form.get("latitude"))
        if request.form.get("longitude", "").strip():
            update_data["longitude"] = float(request.form.get("longitude"))
        
        stations_collection.update_one({"station_id": station_id}, {"$set": update_data})
        flash("Station updated successfully!", "success")
        return redirect(url_for("admin_stations"))
    
    return render_template("admin_edit_station.html", station=station)

@app.route("/admin/stations/delete/<int:station_id>", methods=["POST"])
@admin_required
def admin_delete_station(station_id):
    """Admin - delete station"""
    submitted_token = request.form.get("csrf_token", "")
    if not submitted_token or submitted_token != session.get("csrf_token"):
        flash("Your session expired, please try again.", "error")
        return redirect(url_for("admin_stations"))
    
    station = stations_collection.find_one({"station_id": station_id})
    if not station:
        flash("Station not found.", "error")
        return redirect(url_for("admin_stations"))
    
    bookings_collection.delete_many({"station_id": station_id})
    reviews_collection.delete_many({"station_id": station_id})
    favorites_collection.delete_many({"station_id": station_id})
    stations_collection.delete_one({"station_id": station_id})
    
    flash(f"Station '{station['name']}' deleted successfully!", "success")
    return redirect(url_for("admin_stations"))

@app.route("/admin/users")
@admin_required
def admin_users():
    """Admin - view all users"""
    users_list = list(users_collection.find({}, {"_id": 1, "name": 1, "email": 1, "created_at": 1, "vehicle": 1}).sort("created_at", -1))
    return render_template("admin_users.html", users=users_list)

@app.route("/admin/bookings")
@admin_required
def admin_bookings():
    """Admin - view all bookings"""
    all_bookings = list(bookings_collection.find().sort("created_at", -1))
    return render_template("admin_bookings.html", bookings=all_bookings)

# =========================
# DSA BENCHMARKING
# =========================

@app.route("/benchmark")
def benchmark():
    """DSA Benchmarking - Linear Search & Selection Sort performance"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    # Linear Search Benchmark
    linear_results = []
    search_sizes = [10, 50, 100, 200, 500, 800, 1000]
    
    for n in search_sizes:
        try:
            c_input = str(n) + "\n"
            for i in range(1, n + 1):
                c_input += str(i) + "\n"
            c_input += str(n) + "\n"
            
            start = time.perf_counter()
            process = run_c(SEARCH_EXE, c_input)
            end = time.perf_counter()
            
            elapsed_ms = round((end - start) * 1000, 3)
            found = "FOUND" in process.stdout
            
            linear_results.append({
                "n": n,
                "time_ms": elapsed_ms,
                "found": found
            })
        except Exception as e:
            print(f"Linear search benchmark error at n={n}:", e)
            linear_results.append({
                "n": n,
                "time_ms": 0,
                "found": False
            })
    
    # Selection Sort Benchmark
    sort_results = []
    sort_sizes = [10, 50, 100, 200, 500, 800, 1000]
    
    for n in sort_sizes:
        try:
            c_input = str(n) + "\n"
            import random
            for i in range(1, n + 1):
                dist = round(random.uniform(0.1, 50.0), 1)
                c_input += f"{i} {dist}\n"
            
            start = time.perf_counter()
            process = run_c(SORT_EXE, c_input)
            end = time.perf_counter()
            
            elapsed_ms = round((end - start) * 1000, 3)
            comparisons = len(process.stdout.strip().splitlines())
            
            sort_results.append({
                "n": n,
                "time_ms": elapsed_ms,
                "comparisons": comparisons
            })
        except Exception as e:
            print(f"Selection sort benchmark error at n={n}:", e)
            sort_results.append({
                "n": n,
                "time_ms": 0,
                "comparisons": 0
            })
    
    return render_template("benchmark.html", 
        linear_results=linear_results,
        sort_results=sort_results
    )

if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    app.run(debug=debug_mode)