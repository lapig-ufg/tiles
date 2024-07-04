# gee-tiles


This API is designed to create map tiles in the X, Y, Z format from assets in Google Earth Engine, primarily for Sentinel collections. It sets up a FastAPI application that connects to Google Earth Engine, handles fast JSON responses using ORJSON, initializes the database, and provides endpoints for generating these map tiles.


```sh
 cd tiles-client && ng build --base-href "/" && rsync -av --delete dist/tiles ../site/browser/ && cd ../ && npx angular-cli-ghpages --dir=site/browser
 ```
