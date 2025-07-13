# Credenciais do Google Earth Engine

Este diretório deve conter o arquivo `gee.json` com as credenciais de serviço do Google Earth Engine.

## Como obter as credenciais:

1. Acesse o [Google Cloud Console](https://console.cloud.google.com/)
2. Crie ou selecione um projeto
3. Ative a API do Earth Engine
4. Crie uma conta de serviço
5. Gere uma chave JSON para a conta de serviço
6. Salve o arquivo como `gee.json` neste diretório

## Estrutura esperada do arquivo:

```json
{
  "type": "service_account",
  "project_id": "seu-projeto",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  "client_email": "sua-conta-servico@seu-projeto.iam.gserviceaccount.com",
  "client_id": "...",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "..."
}
```

**IMPORTANTE**: Nunca commite este arquivo no repositório!