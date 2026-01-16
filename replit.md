# GScript Business Application

## Overview
A Flask-based business management application with features for contracts, warehouse management, commercials, price lists, and archive management. Uses PostgreSQL for data storage and includes Telegram bot integration.

## Project Structure
- `app.py` - Main Flask application with routes and business logic
- `backend/` - Backend modules
  - `db.py` - Database configuration and session management
  - `models.py` - SQLAlchemy ORM models
  - `services/` - Business logic services (contracts, warehouse, commercials, etc.)
- `templates/` - Jinja2 HTML templates
- `static/` - Static assets (CSS, JS)
- `telegram_bot.py` - Telegram bot integration

## Technology Stack
- **Framework**: Flask 3.0.3
- **Database**: PostgreSQL (via psycopg2-binary)
- **ORM**: SQLAlchemy 2.0.36
- **Auth**: Flask-Login
- **Migrations**: Flask-Migrate

## Running the Application
The app runs on port 5000 with Flask's development server:
```bash
python app.py
```

## Environment Variables
- `DATABASE_URL` - PostgreSQL connection string (auto-configured by Replit)
- `SECRET_KEY` - Flask secret key (auto-generated if not set)
- `ADMIN_EMAIL` - Email for admin user
- `TELEGRAM_BOT_TOKEN` - For Telegram bot integration

## Recent Changes
- January 2026: Initial Replit setup and configuration
