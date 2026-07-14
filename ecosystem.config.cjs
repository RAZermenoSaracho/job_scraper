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
    },
  ],
};
