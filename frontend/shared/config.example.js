// Exemple pour le développement local : copier en config.js et remplacer les valeurs.
// En production, config.js est généré par infra/s3_site.py au déploiement (ne pas le commiter).
window.DOURA_CONFIG = {
  region: "us-east-1",
  stage: "local",
  apiUrl: "https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/v1",
  userPoolId: "us-east-1_XXXXXXXXX",
  userPoolClientId: "xxxxxxxxxxxxxxxxxxxxxxxxxx",
  defaultAgency: "AG-TUN-03",
  refreshSeconds: 5
};
