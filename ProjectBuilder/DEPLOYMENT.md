# Deployment Guide

## Quick Start: Deploy to Render

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/ProjectBuilder.git
git push -u origin main
```

### 2. Deploy to Render
1. Go to [render.com](https://render.com) and sign in with GitHub
2. Click "New +" → "Web Service"
3. Connect your GitHub repo
4. Fill in:
   - **Name:** projectbuilder
   - **Environment:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `streamlit run app.py --server.port 8080 --server.address 0.0.0.0`
5. Add environment variables:
   - `GROQ_API_KEY`: Your Groq API key (get from [console.groq.com](https://console.groq.com))
6. Click "Create Web Service"
7. Your app will be available at `https://<service-name>.onrender.com`

---

## Deploy to Fly.io

### 1. Install Fly CLI
```bash
# Windows (using Chocolatey)
choco install flyctl

# macOS
brew install flyctl

# Linux
curl -L https://fly.io/install.sh | sh
```

### 2. Login and Deploy
```bash
flyctl auth login
flyctl launch
```

During setup:
- Select yes for Dockerfile (already included)
- Set app name
- Select a region

### 3. Set Environment Variables
```bash
flyctl secrets set GROQ_API_KEY="your_key_here"
```

### 4. Deploy
```bash
flyctl deploy
```

Your app will be at `https://<app-name>.fly.dev`

---

## Deploy to Railway

### 1. Go to [railway.app](https://railway.app)
1. Sign in with GitHub
2. Click "New Project"
3. Select "Deploy from GitHub repo"
4. Connect and authorize your GitHub account
5. Select the ProjectBuilder repository

### 2. Configure
1. Railway will auto-detect it's a Python app
2. Add environment variable: `GROQ_API_KEY`
3. In "Settings" → "Networking" → ensure port 8501 is exposed
4. Restart deployment

Your app will be available at the generated Railway URL.

---

## Deploy via Docker (Any Cloud Provider)

### 1. Build Docker Image
```bash
docker build -t projectbuilder:latest .
```

### 2. Tag for Docker Registry (DockerHub)
```bash
docker tag projectbuilder:latest YOUR_DOCKERHUB_USERNAME/projectbuilder:latest
docker login
docker push YOUR_DOCKERHUB_USERNAME/projectbuilder:latest
```

### 3. Deploy Anywhere
- **AWS ECS**: Use the image URI
- **Google Cloud Run**: `gcloud run deploy projectbuilder --image YOUR_DOCKERHUB_USERNAME/projectbuilder:latest --port 8501`
- **Azure Container Instances**: Use the image URI
- **DigitalOcean App Platform**: Connect GitHub repo, auto-builds and deploys

---

## GitHub Actions Auto-Deploy (Optional)

Update `.github/workflows/ci.yml` to auto-deploy after tests pass:

```yaml
name: CI/CD

on:
  push:
    branches: [ main ]

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.12'
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
    
    - name: Run tests
      run: pytest -q
    
    - name: Build Docker image
      run: |
        docker build -t projectbuilder:latest .
    
    - name: Push to Docker Hub (optional)
      if: github.ref == 'refs/heads/main'
      run: |
        echo ${{ secrets.DOCKER_PASSWORD }} | docker login -u ${{ secrets.DOCKER_USERNAME }} --password-stdin
        docker tag projectbuilder:latest ${{ secrets.DOCKER_USERNAME }}/projectbuilder:latest
        docker push ${{ secrets.DOCKER_USERNAME }}/projectbuilder:latest
    
    - name: Deploy to Render (optional)
      if: github.ref == 'refs/heads/main'
      run: |
        curl -X POST https://api.render.com/deploy/srv-${{ secrets.RENDER_SERVICE_ID }}?key=${{ secrets.RENDER_API_KEY }}
```

**Setup GitHub Secrets:**
1. Go to repo Settings → Secrets and variables → Actions
2. Add:
   - `DOCKER_USERNAME`: Your DockerHub username
   - `DOCKER_PASSWORD`: Your DockerHub token
   - `RENDER_SERVICE_ID`: From Render dashboard
   - `RENDER_API_KEY`: From Render account settings

---

## Environment Variables Required

All deployment targets need:
- `GROQ_API_KEY`: Your Groq API key

Optional:
- `SECRET_KEY`: For JWT (defaults to 'dev_secret_key', change in production)

---

## Troubleshooting

### Port Issues
- Streamlit defaults to port 8501
- Most cloud platforms auto-expose ports
- Render/Fly.io: Specify `--server.port 8080 --server.address 0.0.0.0`
- Railway: Auto-detects Streamlit

### Database Persistence
- SQLite database is stored in-container (will be lost on restart)
- For production: Use PostgreSQL (modify `app.py` to use `psycopg2` instead of `sqlite3`)

### API Key Security
- Never commit `.env` files
- Use platform-specific secrets management
- Rotate keys regularly

---

## Production Recommendations

1. **Use PostgreSQL** instead of SQLite
2. **Add rate limiting** for AI generation calls
3. **Enable HTTPS** (automatic on Render/Fly.io/Railway)
4. **Monitor logs** via cloud platform dashboards
5. **Set resource limits** (memory, CPU) appropriately
6. **Use a CDN** for static assets if needed
7. **Implement session timeout** for security

