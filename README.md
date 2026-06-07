# The Bill Mate 🧾✨

The Bill Mate is a smart, incredibly simple web application that helps you organize your financial life. Instead of manually typing out receipts and bills, you just upload a photo. Our AI scans it, pulls out all the important details (like vendor name, items, tax, and total), and saves it securely to your account.

*💡 Tip: Add a nice wide banner image of your app here!*
`![App Banner](replace-with-your-banner-image-url.png)`

---

## What makes it cool?

- **Magic AI Scanning:** Snap a picture of a receipt, and our SambaNova AI model will read it like a human, automatically extracting every single item and price.
- **Financial Dashboard:** See all your spending habits at a glance. We calculate your overall totals, monthly totals, and vendor counts automatically.
- **Secure Cloud Storage:** Everything is tied directly to your Google account using Firebase. Your bills are saved securely in MongoDB Atlas, meaning you never lose a receipt again.
- **Professional Exports:** Need to print a receipt for an expense report? We generate a beautiful, branded, printable PDF for any bill in one click.

*💡 Tip: Add a screenshot of the dashboard and metrics cards here!*
`![Dashboard Screenshot](replace-with-dashboard-image-url.png)`

---

## 🛠️ The Tech Stack

I built this app to be fast, secure, and modern:
- **Frontend:** Vanilla HTML/JS with TailwindCSS (for that sleek glassmorphic design)
- **Backend:** Python FastAPI (blazing fast and handles the AI logic)
- **AI Engine:** SambaNova (using the Llama/Gemma models for optical character recognition)
- **Database:** MongoDB Atlas
- **Authentication:** Google Firebase (Client + Admin SDK)

---

## 🚀 Running it on your computer

Want to run The Bill Mate locally? It's super easy.

### 1. What you need first
Make sure you have Python installed. You'll also need:
- A MongoDB Atlas connection string.
- A SambaNova API key.
- A Firebase project setup (with Google Sign-In enabled).

### 2. Setup the environment
1. Open your terminal and clone the repo.
2. Create a virtual environment and install the requirements:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file in the root directory and add your secret keys:
   ```env
   MONGO_URI=your_mongodb_connection_string
   SAMBANOVA_API_KEY=your_sambanova_api_key
   ```
4. Download your Firebase Admin private key (it's a JSON file) and save it as `firebase-admin.json` in the root folder. *(Don't worry, it's ignored by git so you won't accidentally upload it!)*

### 3. Run the app
Start the FastAPI server:
```bash
uvicorn main:app --reload
```
Then, just open `http://localhost:8000` in your web browser!

*💡 Tip: Add an image or GIF showing the drag-and-drop bill scanning process here!*
`![Scanning GIF](replace-with-scanning-gif-url.gif)`

---

## ☁️ Deploying to the Web

This app is production-ready and configured to deploy instantly on **Render**.

1. Connect your GitHub repository to Render as a "Web Service".
2. Render will automatically read the `render.yaml` and `Procfile`.
3. Go to the "Environment" tab in Render:
   - Add your `.env` variables (`MONGO_URI`, `SAMBANOVA_API_KEY`).
   - Go to "Secret Files", name a file `firebase-admin.json`, and paste your Firebase key contents inside it.
   - Add an environment variable `FIREBASE_CREDS_PATH` set to `/etc/secrets/firebase-admin.json`.
4. Deploy!

---
*Created with ❤️ for seamless financial management.*
