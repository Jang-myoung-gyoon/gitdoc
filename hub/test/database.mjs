import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';

export function database(path=':memory:'){
  const raw=new DatabaseSync(path);
  raw.exec('PRAGMA foreign_keys=ON');
  if(!raw.prepare("SELECT name FROM sqlite_master WHERE name='repos'").get()){
    raw.exec(readFileSync(new URL('../drizzle/0000_hub.sql',import.meta.url),'utf8'));
  }
  return {
    prepare(query){
      const statement=raw.prepare(query);
      let args=[];
      return {
        bind(...values){args=values;return this;},
        async first(){return statement.get(...args)??null;},
        async all(){return {results:statement.all(...args)};},
        syncRun(){return {meta:statement.run(...args)};},
        async run(){return this.syncRun();},
      };
    },
    async batch(commands){
      raw.exec('BEGIN');
      try{const results=commands.map(command=>command.syncRun());raw.exec('COMMIT');return results;}
      catch(error){raw.exec('ROLLBACK');throw error;}
    }
  };
}
