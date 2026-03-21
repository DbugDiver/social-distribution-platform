[![Review Assignment Due Date](https://classroom.github.com/assets/deadline-readme-button-22041afd0340ce965d47ae6ef1cefeee28c7c493a6346c4f15d667ab976d596c.svg)](https://classroom.github.com/a/bRZK9dqv)

# CMPUT 404 Project By Team Honeydew

This repository contains the group project for CMPUT 404 - Team Honeydew at the University of Alberta.

See the [project description](https://uofa-cmput404.github.io/general/project.html) for full details.

The goal of this project is to build a **fully functional website that matches all the requirements for the course project**.

---

## Team Name

### "Honeydew"
__uofa-cmput404-team-honeydew__

## Team Members
- Rosy Budhathoki (rosybudhathoki)
- Gui Carius (gCarius)
- Manas Joshi (mjoshi3)
- Dbug Diver
- Nathan Rodrigues (narodri1)

> Names may be aliases.
  
## Run the following commands to run the web app:
- python3 -m venv .venv
- source .venv/bin/activate
- pip install -r requirements.txt
- pip install django
- python manage.py makemigrations
- python manage.py migrate
- python manage.py createsuperuser -> create username and password. This will be required to login
- python manage.py runserver

## Run the following commands to run the tests:
- python3 -m venv .venv
- source .venv/bin/activate
- pip install -r requirements.txt
- python manage.py test

## Federation setup (Part 3)

Each node has its own database. Nodes sync behavior through API calls, not shared DB state.

### Local federation test with 2 nodes (PowerShell)

Terminal 1 (node1)
- `$env:DEBUG="True"`
- `$env:SITE_URL="http://127.0.0.1:8000"`
- `$env:ALLOWED_HOSTS="127.0.0.1,localhost"`
- `$env:REMOTE_NODES="http://127.0.0.1:8001"`
- `$env:CSRF_TRUSTED_ORIGINS="http://127.0.0.1:8000,http://127.0.0.1:8001"`
- `python manage.py migrate`
- `python manage.py runserver 8000`

Terminal 2 (node2)
- `$env:DEBUG="True"`
- `$env:SITE_URL="http://127.0.0.1:8001"`
- `$env:ALLOWED_HOSTS="127.0.0.1,localhost"`
- `$env:REMOTE_NODES="http://127.0.0.1:8000"`
- `$env:CSRF_TRUSTED_ORIGINS="http://127.0.0.1:8000,http://127.0.0.1:8001"`
- `python manage.py migrate`
- `python manage.py runserver 8001`

Optional (for protected remote endpoints)
- `$env:REMOTE_NODE_CREDENTIALS='{"http://127.0.0.1:8000":{"username":"node1","password":"pass1"},"http://127.0.0.1:8001":{"username":"node2","password":"pass2"}}'`

### Heroku federation environment

Set these vars on each Heroku app (values differ per node):
- `DEBUG=False`
- `SITE_URL=https://<your-app>.herokuapp.com`
- `ALLOWED_HOSTS=<your-app>.herokuapp.com`
- `REMOTE_NODES=https://node-a.herokuapp.com,https://node-b.herokuapp.com`
- `CSRF_TRUSTED_ORIGINS=https://<your-app>.herokuapp.com,https://node-a.herokuapp.com,https://node-b.herokuapp.com`
- `REMOTE_NODE_CREDENTIALS={"https://node-a.herokuapp.com":{"username":"...","password":"..."},"https://node-b.herokuapp.com":{"username":"...","password":"..."}}`

These settings are required so APIs generate correct HTTPS absolute URLs on Heroku and federation requests authenticate correctly.

## License
This project is licensed under the **MIT License**.  
See the `LICENSE` file for details.