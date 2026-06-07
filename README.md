<div align="center">
  <!-- Place your Logo here! Replace 'Logo.png' if it's named differently -->
  <img src="templates/Logo.png" alt="The Bill Mate Logo" width="120" height="120">

  # The Bill Mate 🧾✨
  
  **🌍 Live Demo:** [https://thebillmate.j4du.in/](https://thebillmate.j4du.in/)

  <p><em>An intelligent, AI-powered financial ledger that transforms physical receipts into pristine, actionable data.</em></p>
</div>

---

## 📖 What is The Bill Mate?
**The Bill Mate** is a modern web application designed to eliminate manual data entry. Whether you are managing personal expenses, tracking corporate invoices, or digitizing a shoebox full of receipts, The Bill Mate uses multimodal AI to instantly "read" your documents like a human would. 

It automatically extracts the vendor name, date, line items, taxes, and totals, then securely stores everything in a searchable, printable cloud archive.

---

## ✨ Features

- **Automated Data Extraction (AI OCR):** Powered by SambaNova AI (Gemma/Llama models), it instantly identifies and extracts complex line items from photos of bills or uploaded images.
- **Real-Time Financial Dashboard:** Calculates your overall total spending, current month spending, and unique vendor counts automatically.
- **Enterprise-Grade Security:** User authentication is handled by Google Firebase, and your financial ledgers are securely stored and isolated in MongoDB Atlas.
- **Printable Receipts:** Click one button to instantly generate a clean, professional, branded PDF printout of any past bill.
- **Manual Adjustments:** An intuitive UI allows you to manually add, edit, or correct any line items if the physical receipt is blurry or damaged.

---

## 📸 Application Showcase

> **Note for Developer:** Replace the placeholder links below with actual screenshots of your app by dragging and dropping images directly into GitHub!

### 1. Scanning Area
*The intuitive interface for capturing receipts via webcam or file upload.*
`![Scanning Area Screenshot](<img width="1901" height="935" alt="Screenshot 2026-06-07 160426" src="https://github.com/user-attachments/assets/8baf65d9-207c-4083-8369-a731d2e911f0" />
)`

### 2. The Dashboard
*Your financial metrics at a glance, calculated dynamically.*

### 3. Bill History Archive
*The complete ledger of every receipt scanned and digitized.*
`![History Screenshot](<img width="1901" height="875" alt="Screenshot 2026-06-07 160459" src="https://github.com/user-attachments/assets/e6ec476e-e62d-4826-af22-b4184ba85207" />
)`

---

## 🛠️ The Tech Stack

- **Frontend:** Vanilla HTML/JS with TailwindCSS for a sleek, responsive, glassmorphic UI.
- **Backend:** Python FastAPI for blazing fast performance.
- **AI Engine:** SambaNova AI.
- **Database:** MongoDB Atlas (Async Motor client).
- **Authentication:** Google Firebase.

---

## 🚀 Running Locally

1. **Clone the repository.**
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Configure Environment:** Create a `.env` file with:
   ```env
   MONGO_URI=your_mongodb_connection_string
   SAMBANOVA_API_KEY=your_sambanova_api_key
   ```
4. **Configure Firebase:** Save your Firebase Admin private key as `firebase-admin.json` in the root directory.
5. **Start the server:**
   ```bash
   uvicorn main:app --reload
   ```
   Open `http://localhost:8000` in your browser.

---

