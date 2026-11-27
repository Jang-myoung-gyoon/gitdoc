CREATE TABLE repos(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,owner TEXT NOT NULL,created INTEGER NOT NULL);
--> statement-breakpoint
CREATE TABLE members(repo TEXT NOT NULL REFERENCES repos(id),uid TEXT NOT NULL,email TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN('owner','write','read')),PRIMARY KEY(repo,uid));
--> statement-breakpoint
CREATE TABLE invites(id TEXT PRIMARY KEY,repo TEXT NOT NULL REFERENCES repos(id),email TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN('write','read')),expires INTEGER NOT NULL,accepted INTEGER);
--> statement-breakpoint
CREATE TABLE branches(repo TEXT NOT NULL REFERENCES repos(id),name TEXT NOT NULL,parent TEXT,created INTEGER NOT NULL,PRIMARY KEY(repo,name));
--> statement-breakpoint
CREATE TABLE documents(repo TEXT NOT NULL REFERENCES repos(id),branch TEXT NOT NULL,path TEXT NOT NULL,state TEXT NOT NULL,rev INTEGER NOT NULL DEFAULT 0,head TEXT,head_rev INTEGER,PRIMARY KEY(repo,branch,path),FOREIGN KEY(repo,branch) REFERENCES branches(repo,name));
--> statement-breakpoint
CREATE TABLE commits(id TEXT PRIMARY KEY,repo TEXT NOT NULL,branch TEXT NOT NULL,path TEXT NOT NULL,parent TEXT,state TEXT NOT NULL,message TEXT NOT NULL,actor TEXT NOT NULL,created INTEGER NOT NULL,FOREIGN KEY(repo,branch,path) REFERENCES documents(repo,branch,path));
--> statement-breakpoint
CREATE INDEX commits_doc ON commits(repo,branch,path,created DESC);
--> statement-breakpoint
CREATE TABLE tickets(id TEXT PRIMARY KEY,repo TEXT NOT NULL REFERENCES repos(id),kind TEXT NOT NULL CHECK(kind IN('issue','proposal')),title TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',source TEXT,target TEXT,path TEXT,actor TEXT NOT NULL,created INTEGER NOT NULL);
--> statement-breakpoint
CREATE TABLE comments(id TEXT PRIMARY KEY,ticket TEXT NOT NULL REFERENCES tickets(id),body TEXT NOT NULL,actor TEXT NOT NULL,created INTEGER NOT NULL);
--> statement-breakpoint
CREATE TABLE releases(repo TEXT NOT NULL REFERENCES repos(id),tag TEXT NOT NULL,commit_id TEXT NOT NULL REFERENCES commits(id),notes TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(repo,tag));
--> statement-breakpoint
CREATE TABLE stars(repo TEXT NOT NULL REFERENCES repos(id),uid TEXT NOT NULL,PRIMARY KEY(repo,uid));
--> statement-breakpoint
CREATE TABLE activity(id TEXT PRIMARY KEY,repo TEXT NOT NULL REFERENCES repos(id),actor TEXT NOT NULL,event TEXT NOT NULL,detail TEXT NOT NULL,created INTEGER NOT NULL);
--> statement-breakpoint
CREATE INDEX activity_repo ON activity(repo,created DESC);
