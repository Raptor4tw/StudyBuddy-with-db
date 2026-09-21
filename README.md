# StudyBuddy - AI Learning Partner

An AI-powered study assistant that quizzes you on your own lecture materials using RAG and LLM feedback.

**[▶ Try the live app](https://studybuddy-ibrahim-zarouri.streamlit.app)** · **[Project website](https://ibrahimzarouri.github.io/study-buddy/)** · **[Technical documentation](DOCUMENTATION.md)**

No setup needed to try it: open the live app, upload a lecture PDF, and start explaining.

## How it works

1. Upload your lecture notes or slides (PDF, Word, or plain text)
2. StudyBuddy extracts the main topics automatically
3. Select a topic - StudyBuddy asks you a comprehension question based on your material
4. Explain the concept in your own words
5. Get instant feedback and targeted follow-up questions until the topic is mastered

Anything you answer only partially or get wrong is collected in a **review list** in the
sidebar - with the question, your answer, and the concept you missed - so you can come
back to it after moving on to other topics.

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-username/study-buddy.git
cd study-buddy
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure your API key

Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
```

Then open `.env` and set your values:

```
API_KEY=your-api-key-here
BASE_URL=https://chat.kiconnect.nrw/api/v1
MODEL=OpenAI-GPT-5-Mini
```

> If you are using a different OpenAI-compatible API, replace `BASE_URL` and `MODEL` accordingly.

### 4. Run the app

Open your terminal (Command Prompt or PowerShell) and navigate to the project folder:

```bash
cd "C:\path\to\study-buddy"
```

For example, if you cloned it to your D drive:

```bash
cd "D:\study-buddy"
```

Then run:

```bash
streamlit run app.py
```

The app will open automatically in your browser at `http://localhost:8501`.

## Run with Docker (alternative)

If you have [Docker](https://www.docker.com/products/docker-desktop/) installed, you can skip the Python setup entirely. You still need the `.env` file from step 3 - it is passed to the container at runtime and never baked into the image.

From the project folder, run:

```bash
docker compose up --build
```

Then open `http://localhost:8501` in your browser.

Or without Docker Compose:

```bash
docker build -t studybuddy .
docker run --env-file .env -p 8501:8501 studybuddy
```

> The first build downloads the ML dependencies and the embedding model, so it can take several minutes. Later builds reuse the cache and are fast.

## Deploy your own

The app is deployed on [Streamlit Community Cloud](https://share.streamlit.io), which runs the Python backend directly from this repository. (GitHub Pages hosts only static files and cannot run Streamlit, so it serves the project website in `docs/` instead.)

1. Fork this repository.
2. Go to [share.streamlit.io](https://share.streamlit.io) and choose **Create app**, then pick your fork, branch `main`, and main file `app.py`.
3. Under **Advanced settings**, paste your credentials into **Secrets**:

   ```toml
   API_KEY = "your-api-key"
   BASE_URL = "https://chat.kiconnect.nrw/api/v1"
   MODEL = "OpenAI-GPT-5-Mini"
   ```

4. Click **Deploy**.

Secrets set here are read via `st.secrets` and mirrored into the environment at startup, so the same code runs unchanged locally (`.env`), in Docker (`--env-file`), and in the cloud. The first start takes a few minutes because the embedding model is downloaded once.

## Running tests

The project includes a unit test suite. From the project folder, run:

```bash
python -m pytest tests/ -v
```

## Supported file types

- PDF (`.pdf`)
- Word (`.docx`)
- Plain text (`.txt`)

## Documentation

See [DOCUMENTATION.md](DOCUMENTATION.md) for the technical documentation: architecture, processing pipeline, prompt design, testing, and design decisions.
