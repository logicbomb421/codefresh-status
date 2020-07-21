.PHONY: clean build-develop build assets

clean:
	rm -rf build/ dist/

build-develop: clean
	python3 setup.py py2app --alias

run-develop: build-develop
	./dist/CodefreshStatus.app/Contents/MacOS/CodefreshStatus

build: clean
	python3 setup.py py2app

assets:
	mkdir ./dist/CodefreshStatus.app/Contents/Resources/assets
	cp ./dist/CodefreshStatus.app/Contents/Resources/*.png ./dist/CodefreshStatus.app/Contents/Resources/assets

release: build assets
	zip -r ./dist/CodefreshStatus.app.zip ./dist/CodefreshStatus.app/
