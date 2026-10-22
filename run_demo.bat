@echo off
python setup_db.py
python app.py --demo
python evaluate_queries.py
python -m unittest discover -s tests -v
pause
