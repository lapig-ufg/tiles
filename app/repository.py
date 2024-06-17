from sqlalchemy.orm import Session

from app.models import Layer

class LayerRepository:
    @staticmethod
    def find_all(db: Session) -> list[Layer]:
        return db.query(Layer).all()

    @staticmethod
    def save(db: Session, layer: Layer) -> Layer:
        if layer.layer:
            db.merge(layer)
        else:
            db.add(layer)
        db.commit()
        return layer

    @staticmethod
    def find_by_layer(db: Session, layer: int) -> Layer:
        return db.query(Layer).filter(Layer.layer == layer).first()

    @staticmethod
    def exists_by_layer(db: Session, layer: int) -> bool:
        return db.query(Layer).filter(Layer.layer == layer).first() is not None

    @staticmethod
    def delete_by_layer(db: Session, layer: int) -> None:
        layer = db.query(Layer).filter(Layer.layer == layer).first()
        if layer is not None:
            db.delete(layer)
            db.commit()