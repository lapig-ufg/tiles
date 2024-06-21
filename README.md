# gee-tiles


This API is designed to create map tiles in the X, Y, Z format from assets in Google Earth Engine, primarily for Sentinel collections. It sets up a FastAPI application that connects to Google Earth Engine, handles fast JSON responses using ORJSON, initializes the database, and provides endpoints for generating these map tiles.


```sh
 ng build --base-href "/tiles/" && rsync -av --delete dist/tiles ../titles_eco2/site/browser/ && cd ../titles_eco2 && npx angular-cli-ghpages --dir=site/browser
 ```
