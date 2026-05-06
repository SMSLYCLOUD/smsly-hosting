# Contributing to Grid

We are building the **Universal PaaS** for the world. Whether you are in San Francisco, Lagos, Berlin, or Tokyo, we want your code.

## 🌟 Bounty Program
We incentivize critical features. Check our [Issues](https://github.com/SMSLYCLOUD/smsly-hosting/issues) for "Bounty" labels.
- **$50 - $500**: Creating new Cloud Adapters (e.g., DigitalOcean, Linode).
- **$100**: Dashboard UI improvements.
- **$1000**: Core architecture upgrades (e.g., Service Mesh integration).

## 🛠️ Development Setup

### Prerequisites
- Docker & Docker Compose
- Python 3.12+
- Node.js 20+

### Steps
1. **Fork & Clone**
   ```bash
   git clone https://github.com/YOUR_USERNAME/smsly-hosting.git
   cd smsly-hosting
   ```

2. **Start Backend**
   ```bash
   cd backend
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py runserver
   ```

3. **Start Frontend**
   ```bash
   cd frontend
   npm install
   npm run dev
   ```

## 🧪 Testing
We enforce strict quality standards.
- **Backend Tests**: `pytest` (Must pass CI).
- **Linting**: `pylint` (Must score > 9.0).

## 🌍 Global Mission
Our goal is to democratize cloud infrastructure. Avoid region-specific hardcoding unless explicitly handling latency optimization. Ensure UI supports i18n where possible.
