const fs = require("fs");

// Path to the web3.career API token file (the value is never committed). Read at pm2
// startup; if the file is missing/unreadable the variable stays unset and the web3career
// API adapter reports itself non-executable in GET /sources.
const WEB3CAREER_TOKEN_FILE = "/Users/razs/production/odoo_src/web3career_api_token";

function readToken(path) {
  try {
    return fs.readFileSync(path, "utf8").trim() || undefined;
  } catch (err) {
    return undefined;
  }
}

const web3careerToken = readToken(WEB3CAREER_TOKEN_FILE);

module.exports = {
  apps: [
    {
      name: "job-scraper-api",
      cwd: "/Users/razs/production/job_scraper",
      script: "/usr/local/anaconda3/envs/job_scraper/bin/uvicorn",
      args: "app:app --host 127.0.0.1 --port 8420",
      interpreter: "none",
      autorestart: true,
      watch: false,
      env: web3careerToken ? { WEB3CAREER_API_TOKEN: web3careerToken } : {},
    },
  ],
};
