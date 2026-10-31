"""Küçük bir market için stok takibi."""
from dataclasses import dataclass, field


@dataclass
class Product:
    """Stoktaki tek bir ürün."""

    name: str
    quantity: int
    price: float
    tags: list = field(default_factory=list)


class Warehouse:
    """Ürünleri adına göre saklayan basit depo."""

    def __init__(self):
        self._products = {}

    def add(self, product):
        """Ürünü ekler; aynı ad varsa adedi artırır."""
        existing = self._products.get(product.name)
        if existing is None:
            self._products[product.name] = product
        else:
            existing.quantity += product.quantity

    def remove(self, name, amount):
        """Stoktan amount kadar düşer; yetersizse ValueError fırlatır."""
        product = self._products[name]
        if product.quantity < amount:
            raise ValueError(f"not enough stock for {name}")
        product.quantity -= amount

    def low_stock(self, threshold=5):
        """Adedi eşiğin altında kalan ürünlerin adlarını döndürür."""
        return [p.name for p in self._products.values() if p.quantity < threshold]

    def total_value(self):
        """Depodaki tüm ürünlerin toplam değerini döndürür."""
        return sum(p.quantity * p.price for p in self._products.values())
