import type { GeoPoint } from "@/types/banking";

export const MALE_FIRST_NAMES = [
  "Aarav", "Rohan", "Bibek", "Suman", "Nabin", "Prakash", "Rajesh", "Dipesh",
  "Kiran", "Santosh", "Manish", "Sujan", "Anish", "Bishal", "Sagar", "Ramesh",
  "Hari", "Gopal", "Krishna", "Deepak", "Niraj", "Ujjwal", "Saroj", "Binod",
  "Pradeep", "Milan", "Ashok", "Sandesh", "Prabin", "Roshan",
];

export const FEMALE_FIRST_NAMES = [
  "Sita", "Anita", "Puja", "Sushmita", "Nisha", "Rita", "Sabina", "Prativa",
  "Manisha", "Sunita", "Aasha", "Bina", "Kabita", "Sarita", "Rekha", "Muna",
  "Sneha", "Alina", "Barsha", "Sabnam", "Pooja", "Srijana", "Anjali", "Bimala",
  "Rojina", "Sadikshya", "Aayushma", "Prerana", "Samjhana", "Ishwori",
];

export const LAST_NAMES = [
  "Shrestha", "Maharjan", "Tamang", "Gurung", "Magar", "Rai", "Limbu", "Thapa",
  "Adhikari", "Poudel", "Karki", "Bhattarai", "Koirala", "Dahal", "Acharya",
  "Sharma", "Basnet", "Khadka", "Pandey", "Joshi", "Bhandari", "Chaudhary",
  "Yadav", "KC", "Lama", "Neupane", "Regmi", "Ghimire", "Subedi", "Baral",
];

export const BANKS = [
  "Global IME Bank",
  "NIC Asia Bank",
  "Nabil Bank",
  "Nepal Investment Mega Bank",
  "NMB Bank",
  "Prabhu Bank",
  "Siddhartha Bank",
  "Machhapuchhre Bank",
  "Kumari Bank",
  "Sanima Bank",
  "Everest Bank",
  "Laxmi Sunrise Bank",
  "Citizens Bank International",
  "Standard Chartered Nepal",
  "Himalayan Bank",
  "Agricultural Development Bank",
  "Rastriya Banijya Bank",
];

export const WALLETS = [
  "eSewa",
  "Khalti",
  "IME Pay",
  "ConnectIPS",
  "Prabhu Pay",
  "Fonepay",
];

export const MERCHANTS = [
  "Bhatbhateni Supermarket",
  "Daraz Nepal",
  "Ncell Axiata",
  "Nepal Telecom",
  "Nepal Electricity Authority",
  "Kathmandu Upatyaka Khanepani",
  "Foodmandu",
  "Pathao Nepal",
  "QFX Cinemas",
  "Big Mart",
  "Salesberry",
  "Sastodeal",
  "Himalayan Java Coffee",
  "KFC Nepal",
  "Bhat-Bhateni Online",
  "Worldlink Communications",
  "Vianet Communications",
  "Sipradi Trading",
  "Hyundai Nepal",
  "Sajha Yatayat",
];

export const CITIES: GeoPoint[] = [
  { city: "Kathmandu", lat: 27.7172, lng: 85.324 },
  { city: "Lalitpur", lat: 27.6588, lng: 85.3247 },
  { city: "Bhaktapur", lat: 27.671, lng: 85.4298 },
  { city: "Pokhara", lat: 28.2096, lng: 83.9856 },
  { city: "Biratnagar", lat: 26.4525, lng: 87.2718 },
  { city: "Birgunj", lat: 27.0104, lng: 84.8821 },
  { city: "Butwal", lat: 27.7006, lng: 83.4484 },
  { city: "Dharan", lat: 26.8065, lng: 87.2846 },
  { city: "Bharatpur", lat: 27.6768, lng: 84.4344 },
  { city: "Nepalgunj", lat: 28.05, lng: 81.6167 },
  { city: "Hetauda", lat: 27.4287, lng: 85.0322 },
  { city: "Janakpur", lat: 26.7288, lng: 85.9266 },
  { city: "Dhangadhi", lat: 28.6949, lng: 80.5825 },
  { city: "Itahari", lat: 26.6646, lng: 87.2718 },
];

// district / province for each domestic city — used for Track B
// customer_profiles alignment (district, province).
export const CITY_DISTRICTS: Record<string, { district: string; province: string }> = {
  Kathmandu: { district: "Kathmandu", province: "Bagmati" },
  Lalitpur: { district: "Lalitpur", province: "Bagmati" },
  Bhaktapur: { district: "Bhaktapur", province: "Bagmati" },
  Pokhara: { district: "Kaski", province: "Gandaki" },
  Biratnagar: { district: "Morang", province: "Koshi" },
  Birgunj: { district: "Parsa", province: "Madhesh" },
  Butwal: { district: "Rupandehi", province: "Lumbini" },
  Dharan: { district: "Sunsari", province: "Koshi" },
  Bharatpur: { district: "Chitwan", province: "Bagmati" },
  Nepalgunj: { district: "Banke", province: "Lumbini" },
  Hetauda: { district: "Makwanpur", province: "Bagmati" },
  Janakpur: { district: "Dhanusha", province: "Madhesh" },
  Dhangadhi: { district: "Kailali", province: "Sudurpashchim" },
  Itahari: { district: "Sunsari", province: "Koshi" },
};

export const FOREIGN_CITIES: GeoPoint[] = [
  { city: "Dubai, UAE", lat: 25.2048, lng: 55.2708 },
  { city: "Kuala Lumpur, MY", lat: 3.139, lng: 101.6869 },
  { city: "Doha, QA", lat: 25.2854, lng: 51.531 },
  { city: "Riyadh, SA", lat: 24.7136, lng: 46.6753 },
  { city: "Seoul, KR", lat: 37.5665, lng: 126.978 },
];

export const BRANCHES = [
  "New Road Branch",
  "Durbarmarg Branch",
  "Pulchowk Branch",
  "Newroad Pokhara Branch",
  "Biratnagar Main Branch",
  "Butwal Branch",
  "Thamel Branch",
  "Kalanki Branch",
  "Baneshwor Branch",
  "Maharajgunj Branch",
];

export const DEVICES = [
  "Samsung Galaxy A54",
  "iPhone 13",
  "Redmi Note 12",
  "Vivo Y21",
  "Oppo Reno 8",
  "iPhone 15 Pro",
  "Realme 11",
  "Web · Chrome 120",
  "Web · Edge 119",
  "Samsung Galaxy S23",
];

export const REMARKS = [
  "Family support",
  "Rent payment",
  "Salary",
  "Loan repayment",
  "Shopping",
  "Tuition fee",
  "Medical",
  "Business payment",
  "Gift",
  "Utility bill",
  "Freelance income",
  "Investment",
];

export const AVATAR_COLORS = [
  "#b91c1c", "#c2410c", "#a16207", "#15803d", "#0f766e",
  "#1d4ed8", "#6d28d9", "#be185d", "#0e7490", "#4d7c0f",
];
