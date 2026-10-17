const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const context={window:{}};vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../webui/search-name.js'),'utf8'),context);
const normalize=context.window.SearchTitles.normalize;
const words=['XXX','WEB-DL','WEB','WEBRIP','x264','x265','H.264','1080p','2160p','4K','AAC2.0','MKV','torrent'];
for(const [input,expected] of [
 ['Wonderful.Escapade.With.A.Chocolate.Dick.XXX.WEB-DL.x264','Wonderful Escapade With A Chocolate Dick'],
 ['The.Game.2.2024.XXX.1080p.WEB-DL.x264.mkv','The Game 2 2024'],
 ['The_Game_2160p_WEB_DL_H.264_AAC2.0','The Game'],
 ['[XXX] The Game (1080p) [x264]','The Game'],
 ['WEBster XXXtreme x264ish','WEBster XXXtreme x264ish'],
 ['Élodie.2.XXX.4K','Élodie 2'],
 ['https://www.jeedoo.com/movie/XXX.WEB-DL/?q=The.Game','https://www.jeedoo.com/movie/XXX.WEB-DL/?q=The.Game'],
 ['XXX.WEB-DL.x264',''],
 ])assert.equal(normalize(input,words),expected,input);
assert.equal(normalize('My.Movie-WEB-GROUP',['WEB','GROUP']),'My Movie');
assert.equal(normalize('Title.A+B.Special',['A+B']),'Title Special');
assert.equal(normalize('The.Game.XXX',[]),'The Game XXX');
const games=['NSW','NSP','NSZ','XCI','PS4','PS5','XBOX360','WIN64'];
for(const [name,expected] of [['Mario.Kart.8.Deluxe.NSW.NSP','Mario Kart 8 Deluxe'],['The.Game.PS5','The Game'],['Example.NSZ','Example'],['Switch.Sports.NSW','Switch Sports'],['NSWesome.Adventure.NSW','NSWesome Adventure']])assert.equal(normalize(name,games),expected);
assert.equal(normalize('A.Game.NSW',[]),'A Game NSW');
console.log('Search normalization: movie tags, game platforms and unchanged originals passed.');
