import os, shutil
from main import SessionLocal, ProjectFile, UPLOAD_DIR, BASE_DIR

OLD_DIR = os.path.join(BASE_DIR, "uploads")

db = SessionLocal()
files = db.query(ProjectFile).all()
moved = 0
for f in files:
    old_path = os.path.join(OLD_DIR, f.stored_name)
    new_path = os.path.join(UPLOAD_DIR, f.stored_name)
    if os.path.exists(old_path) and not os.path.exists(new_path):
        shutil.move(old_path, new_path)
        moved += 1
    if os.path.exists(new_path):
        f.file_path = new_path
db.commit()
db.close()
print(f"迁移完成，共搬动 {moved} 个文件")
