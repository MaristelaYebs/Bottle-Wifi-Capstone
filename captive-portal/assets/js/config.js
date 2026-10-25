//this is to enable multi vendo setup, set to true when multi vendo is supported
var isMultiVendo = false;
// 0 = traditional (client choose a vendo) , 1 = auto select vendo base on hotspot address, 2 = interface name ( this will preserve one hotspot server ip only)
var multiVendoOption = 0;

//list here all node mcu address for multi vendo setup
var multiVendoAddresses = [
	{
		vendoName: "Bottle Vendo - ESP32-CAM", //change accordingly to your vendo name
		vendoIp: "10.10.10.252", //change accordingly to your ESP32-CAM IP
		chargingEnable: false,  //change true if you want to enable charging station
		eloadEnable: false, //change true if you want to enable eloading station
		hotspotAddress: "10.10.10.1", // use for multi vendo option = 1, means your vendo map to this hotspot and autoselect it when client connected to this
		interfaceName: "vlan11-hotspot1" // hotspot interface name preser
	},
	{
		vendoName: "Vendo 2 - ESP8622 Wireless", //change accordingly to your vendo name
		vendoIp: "10.10.10.251", //change accordingly to your vendo ip
		chargingEnable: false,  //change true if you want to enable charging station
		eloadEnable: true //change true if you want to enable eloading station
	},
	{
		vendoName: "Vendo 3 - ESP8622 LAN", //change accordingly to your vendo name
		vendoIp: "10.10.10.253", //change accordingly to your vendo ip
		chargingEnable: false,  //change true if you want to enable charging station
		eloadEnable: false //change true if you want to enable eloading station
	},
	{
		vendoName: "Vendo 4 - ESP32 LAN", //change accordingly to your vendo name
		vendoIp: "10.10.10.254", //change accordingly to your vendo ip
		chargingEnable: true,  //change true if you want to enable charging station
		eloadEnable: false //change true if you want to enable eloading station
	}
];


// Must match ESP32-CAM include/config.h MT_USERNAME_ONLY:
//   loginOption 0  ↔  MT_USERNAME_ONLY true   (empty password)
//   loginOption 1  ↔  MT_USERNAME_ONLY false  (password = voucher)
// Mismatch causes "Invalid voucher" after EXTEND TIME (logout + re-login).
var loginOption = 1;

var dataRateOption = false; //replace true if you enable data rates
//put here the default selected address
// ESP32-CAM bottle-vendo IP (must be reachable from hotspot clients / walled garden)
var vendorIpAddress = "10.10.10.252";

var chargingEnable = false; //replace true if you enable charging, this can be override if multivendo setup

var eloadEnable = false; //replace true if you enable eload, this can be override if multivendo setup

//hide pause time / logout true = you want to show pause / logout button
var showPauseTime = true;

//enable member login, true = if you want to enable member login
var showMemberLogin = false;

//enable extend time button for customers
var showExtendTimeButton = true;

//disable voucher input
var disableVoucherInput = false;

//enable mac address as voucher code
var macAsVoucherCode = false;

var qrCodeVoucherPurchase = false;