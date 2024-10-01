#!/bin/bash

# Fail immediately if a command fails
set -e

# Build do projeto Angular e fazer deploy para GitHub Pages
echo "Iniciando o build e deploy do projeto..."
ng build --base-href "/tiles/" && npx angular-cli-ghpages --dir=dist/tiles

echo "Deploy concluído com sucesso!"