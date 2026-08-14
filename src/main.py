from labeling_app import LabelingApp
from service_layer.migration_service import migrate_layout

if __name__ == "__main__":
    print("Labeling App starting...")
    # Bring a pre-rename on-disk layout (Labeled_data/, <video>/data/, Videos/)
    # up to date before anything reads a path. Idempotent and never raises.
    migrate_layout()
    app = LabelingApp()
    app.mainloop()
